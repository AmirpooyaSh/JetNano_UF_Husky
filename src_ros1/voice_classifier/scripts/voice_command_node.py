#!/usr/bin/env python3
"""
Continuous voice-command node (WhisperLive client).

Microphone -> WhisperLive server (WebSocket, port 9090) -> completed segments
-> Ollama classifier -> ROS topics.

Published topics:
    /voice/transcription   std_msgs/String
    /voice/command         std_msgs/String
    /voice/confidence      std_msgs/Float32
    /voice/classification  std_msgs/String (JSON)
"""

import collections
import json
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pyaudio
import requests
import rospy
import websocket
from std_msgs.msg import Float32, String

WHISPER_RATE = 16000


class StreamResampler:
    """Stateful low-pass + resample of a mono float32 stream to 16 kHz."""

    def __init__(self, input_rate: int, output_rate: int = WHISPER_RATE) -> None:
        self.input_rate = int(input_rate)
        self.output_rate = int(output_rate)
        self.bypass = self.input_rate == self.output_rate
        if self.bypass:
            return

        self.step = self.input_rate / float(self.output_rate)
        ratio = max(1.0, self.step)
        taps = int(16 * ratio) | 1
        cutoff = 0.45 / ratio
        n = np.arange(taps) - (taps - 1) / 2.0
        kernel = np.sinc(2.0 * cutoff * n) * np.hamming(taps)
        self.kernel = (kernel / kernel.sum()).astype(np.float32)
        self.tail = np.zeros(taps - 1, dtype=np.float32)
        self.previous = np.zeros(1, dtype=np.float32)
        self.position = 1.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self.bypass:
            return samples.astype(np.float32)

        buffer = np.concatenate([self.tail, samples.astype(np.float32)])
        filtered = np.convolve(buffer, self.kernel, mode="valid")
        self.tail = buffer[-(len(self.kernel) - 1):]

        stream = np.concatenate([self.previous, filtered])
        last_index = len(stream) - 1
        positions = np.arange(self.position, last_index, self.step)

        if positions.size:
            output = np.interp(positions, np.arange(len(stream)), stream)
            next_position = positions[-1] + self.step
        else:
            output = np.zeros(0, dtype=np.float32)
            next_position = self.position

        self.position = next_position - last_index
        self.previous = stream[-1:]
        return output.astype(np.float32)


class VoiceCommandNode:
    VALID_COMMANDS = {
        "Stop",
        "Slow Down",
        "Proceed",
        "Unknown",
    }

    def __init__(self) -> None:
        # WhisperLive server
        self.whisper_host = rospy.get_param("~whisper_host", "127.0.0.1")
        self.whisper_port = int(rospy.get_param("~whisper_port", 9090))
        self.whisper_model = rospy.get_param("~whisper_model", "base.en")
        self.language = rospy.get_param("~language", "en")
        self.use_vad = bool(rospy.get_param("~use_vad", True))
        self.vad_onset = float(rospy.get_param("~vad_onset", 0.5))
        self.min_silence_duration_ms = int(
            rospy.get_param("~min_silence_duration_ms", 300)
        )
        self.same_output_threshold = int(
            rospy.get_param("~same_output_threshold", 2)
        )
        self.no_speech_thresh = float(rospy.get_param("~no_speech_thresh", 0.45))
        self.max_connection_time = int(
            rospy.get_param("~max_connection_time", 86400)
        )
        self.reconnect_delay = float(rospy.get_param("~reconnect_delay", 2.0))
        self.print_partial = bool(rospy.get_param("~print_partial", False))

        # Ollama classifier
        self.enable_classification = bool(
            rospy.get_param("~enable_classification", True)
        )
        self.ollama_url = rospy.get_param(
            "~ollama_url",
            "http://127.0.0.1:11434/api/chat",
        )
        self.ollama_model = rospy.get_param("~ollama_model", "llama3.2:1b")
        self.ollama_timeout = float(rospy.get_param("~ollama_timeout", 60.0))

        # Microphone
        self.input_device = str(rospy.get_param("~input_device", ""))
        self.input_device_index = int(rospy.get_param("~input_device_index", -1))
        self.sample_rate = int(rospy.get_param("~sample_rate", 16000))
        self.channels = int(rospy.get_param("~channels", 1))
        self.audio_chunk = int(rospy.get_param("~audio_chunk", 1024))

        if self.audio_chunk <= 0:
            raise ValueError("audio_chunk must be greater than zero")

        # Publishers
        self.transcription_publisher = rospy.Publisher(
            "/voice/transcription", String, queue_size=10
        )
        self.command_publisher = rospy.Publisher(
            "/voice/command", String, queue_size=10
        )
        self.confidence_publisher = rospy.Publisher(
            "/voice/confidence", Float32, queue_size=10
        )
        self.classification_publisher = rospy.Publisher(
            "/voice/classification", String, queue_size=10
        )

        self.stop_event = threading.Event()
        self.server_ready = threading.Event()
        self.socket_lock = threading.Lock()
        self.socket: Optional[websocket.WebSocket] = None

        self.classification_queue: "queue.Queue[dict]" = queue.Queue()
        self.processed_keys = set()
        self.processed_order: "collections.deque[Tuple[str, str]]" = (
            collections.deque()
        )
        self.last_partial = ""

        self.resampler = StreamResampler(self.sample_rate)
        self.audio = pyaudio.PyAudio()
        self.stream = self.open_microphone()

        self.threads = [
            threading.Thread(target=self.connection_loop, daemon=True),
            threading.Thread(target=self.capture_loop, daemon=True),
        ]
        if self.enable_classification:
            self.threads.append(
                threading.Thread(target=self.classification_worker, daemon=True)
            )

        rospy.on_shutdown(self.shutdown)

    # ------------------------------------------------------------------
    # Microphone
    # ------------------------------------------------------------------
    def open_microphone(self):
        input_devices = []
        for index in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(index)
            if int(info.get("maxInputChannels", 0)) > 0:
                input_devices.append((index, info))

        rospy.loginfo("Available input devices:")
        for index, info in input_devices:
            rospy.loginfo(
                "  [%d] %s (inputs=%d, default_rate=%.0f)",
                index,
                info.get("name"),
                int(info.get("maxInputChannels", 0)),
                float(info.get("defaultSampleRate", 0.0)),
            )

        device_index: Optional[int] = None
        if self.input_device_index >= 0:
            device_index = self.input_device_index
        elif self.input_device:
            wanted = self.input_device.lower()
            for index, info in input_devices:
                if wanted in str(info.get("name", "")).lower():
                    device_index = index
                    break
            if device_index is None:
                raise RuntimeError(
                    "No input device name contains '{}'".format(self.input_device)
                )

        if device_index is None:
            rospy.loginfo("Using default input device.")
        else:
            rospy.loginfo(
                "Using input device [%d] %s",
                device_index,
                self.audio.get_device_info_by_index(device_index).get("name"),
            )

        return self.audio.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=self.audio_chunk,
        )

    def read_chunk(self) -> np.ndarray:
        data = self.stream.read(self.audio_chunk, exception_on_overflow=False)
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if self.channels > 1:
            samples = samples.reshape(-1, self.channels).mean(axis=1)
        return self.resampler.process(samples)

    def capture_loop(self) -> None:
        rospy.loginfo(
            "Streaming microphone: %d Hz x %d ch -> %d Hz mono",
            self.sample_rate,
            self.channels,
            WHISPER_RATE,
        )
        while not self.stop_event.is_set() and not rospy.is_shutdown():
            try:
                audio = self.read_chunk()
            except Exception as exception:
                if self.stop_event.is_set():
                    return
                rospy.logerr("Microphone read failed: %s", exception)
                time.sleep(0.1)
                continue

            # Keep reading while disconnected so the microphone never overflows.
            if not self.server_ready.is_set() or audio.size == 0:
                continue

            with self.socket_lock:
                ws = self.socket
            if ws is None:
                continue

            try:
                ws.send(audio.tobytes(), opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as exception:
                rospy.logwarn("Sending audio failed: %s", exception)
                self.server_ready.clear()

    # ------------------------------------------------------------------
    # WhisperLive connection
    # ------------------------------------------------------------------
    def connection_options(self, uid: str) -> Dict[str, Any]:
        return {
            "uid": uid,
            "language": self.language,
            "task": "transcribe",
            "model": self.whisper_model,
            "use_vad": self.use_vad,
            "max_clients": 4,
            "max_connection_time": self.max_connection_time,
            "send_last_n_segments": 10,
            "no_speech_thresh": self.no_speech_thresh,
            "clip_audio": False,
            "same_output_threshold": self.same_output_threshold,
            # faster-whisper 1.1.0 VadOptions uses "onset", not "threshold".
            "vad_parameters": {
                "onset": self.vad_onset,
                "min_silence_duration_ms": self.min_silence_duration_ms,
            },
        }

    def connection_loop(self) -> None:
        url = "ws://{}:{}".format(self.whisper_host, self.whisper_port)

        while not self.stop_event.is_set() and not rospy.is_shutdown():
            uid = str(uuid.uuid4())
            try:
                rospy.loginfo("Connecting to WhisperLive at %s...", url)
                ws = websocket.create_connection(url, timeout=10)
                ws.send(json.dumps(self.connection_options(uid)))
                ws.settimeout(None)

                with self.socket_lock:
                    self.socket = ws
                self.reset_segment_state()

                self.receive_loop(ws)
            except Exception as exception:
                if not self.stop_event.is_set():
                    rospy.logwarn("WhisperLive connection error: %s", exception)
            finally:
                self.server_ready.clear()
                with self.socket_lock:
                    ws_to_close = self.socket
                    self.socket = None
                if ws_to_close is not None:
                    try:
                        ws_to_close.close()
                    except Exception:
                        pass

            if not self.stop_event.is_set():
                rospy.loginfo("Reconnecting in %.1f s...", self.reconnect_delay)
                self.stop_event.wait(self.reconnect_delay)

    def receive_loop(self, ws: websocket.WebSocket) -> None:
        while not self.stop_event.is_set() and not rospy.is_shutdown():
            raw_message = ws.recv()
            if not raw_message:
                raise ConnectionError("server closed the connection")

            try:
                message = json.loads(raw_message)
            except (TypeError, ValueError):
                continue

            if message.get("message") == "SERVER_READY":
                rospy.loginfo(
                    "WhisperLive ready (backend=%s). Listening...",
                    message.get("backend", "unknown"),
                )
                self.server_ready.set()
                continue

            if message.get("message") == "DISCONNECT":
                raise ConnectionError("server sent DISCONNECT")

            status = message.get("status")
            if status == "WAIT":
                rospy.logwarn(
                    "WhisperLive is full. Wait time: %s minutes",
                    message.get("message"),
                )
                continue
            if status in ("ERROR", "WARNING"):
                rospy.logwarn("WhisperLive %s: %s", status, message.get("message"))
                if status == "ERROR":
                    raise ConnectionError(str(message.get("message")))
                continue

            segments = message.get("segments")
            if isinstance(segments, list):
                self.process_segments(segments)

    # ------------------------------------------------------------------
    # Segments
    # ------------------------------------------------------------------
    def reset_segment_state(self) -> None:
        self.processed_keys.clear()
        self.processed_order.clear()
        self.last_partial = ""

    def remember_segment(self, key: Tuple[str, str]) -> None:
        self.processed_keys.add(key)
        self.processed_order.append(key)
        while len(self.processed_order) > 500:
            self.processed_keys.discard(self.processed_order.popleft())

    def process_segments(self, segments: List[Dict[str, Any]]) -> None:
        for index, segment in enumerate(segments):
            text = str(segment.get("text", "")).strip()
            if not text:
                continue

            if not bool(segment.get("completed", False)):
                if self.print_partial and text != self.last_partial:
                    self.last_partial = text
                    print("[PARTIAL] {}".format(text), flush=True)
                continue

            start = str(segment.get("start", ""))
            key = (start if start else "segment-{}".format(index), text)
            if key in self.processed_keys:
                continue
            self.remember_segment(key)

            self.transcription_publisher.publish(String(data=text))
            print(
                "\n[TRANSCRIPTION] start={} text={}".format(start, text),
                flush=True,
            )

            if self.enable_classification:
                self.classification_queue.put({"text": text, "start": start})

    # ------------------------------------------------------------------
    # Ollama classification
    # ------------------------------------------------------------------
    def classification_worker(self) -> None:
        while not self.stop_event.is_set() and not rospy.is_shutdown():
            try:
                transcription = self.classification_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            text = transcription["text"]
            start = transcription["start"]
            error_message = None

            try:
                command, confidence = self.classify_with_llama(text)
            except Exception as exception:
                command = "Unknown"
                confidence = 0.0
                error_message = str(exception)
                rospy.logerr(
                    "Llama classification failed for '%s': %s", text, exception
                )

            self.publish_classification(
                text=text,
                start=start,
                command=command,
                confidence=confidence,
                error_message=error_message,
            )

    def classify_with_llama(self, text: str) -> Tuple[str, float]:
        tool_definition = {
            "type": "function",
            "function": {
                "name": "classify_voice_command",
                "description": "Classify the command.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": ["Stop", "Slow Down", "Proceed", "Unknown"],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                    "required": ["command", "confidence"],
                },
            },
        }

        system_prompt = """
You are a safety-oriented robot voice-command classifier.
You must call the classify_voice_command tool exactly once.

Classify the intended meaning of the supplied completed transcription.
Exact command words are not required.

Stop:
The speaker wants the robot to stop moving or remain at its current
position. This includes direct or indirect instructions such as:
"stop", "halt", "freeze", "pause", "wait", "hold on", "hold there",
"stay there", "stay where you are", "don't move", "remain there",
"do not come closer", "don't approach me", "keep your distance",
"that's close enough", "that's far enough", or "no farther".

Slow Down:
The speaker wants the robot to continue moving, but at a lower speed
or with greater caution. This includes direct or indirect instructions
such as:
"slow down", "go slower", "reduce your speed", "not so fast",
"take it slowly", "take it easy", "easy", "careful",
"move carefully", "ease up", "you're going too fast",
"approach slowly", or "come closer slowly".

Proceed:
The speaker wants the robot to begin moving, continue moving, resume
after stopping or slowing, or return to normal unrestricted navigation.
This includes direct or indirect instructions such as:
"proceed", "continue", "resume", "go", "go ahead", "keep going",
"carry on", "move", "start moving", "you can move now",
"you may continue", "come forward", "come closer",
"approach me", "follow me", "keep moving", or "back to normal".

Unknown:
Use Unknown when the transcription is unrelated, conversational,
unclear, incomplete, merely mentions a command, asks a question
without requesting robot motion, or does not express any equivalent
intent.

Handle negation according to its meaning:
- "don't stop" means Proceed.
- "don't continue" means Stop.
- "don't slow down" means Proceed.
- "do not come closer" means Stop.

Classify meaning rather than matching exact keywords.
Do not invent a command when no clear command intent exists.
Confidence must be between 0.0 and 1.0.
""".strip()

        request_body = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Transcription:\n" + text},
            ],
            "tools": [tool_definition],
            "stream": False,
            "keep_alive": -1,
            "options": {"temperature": 0.0},
        }

        response = requests.post(
            self.ollama_url, json=request_body, timeout=self.ollama_timeout
        )
        response.raise_for_status()

        tool_calls = response.json().get("message", {}).get("tool_calls", [])
        if not tool_calls:
            return "Unknown", 0.0

        function_data = tool_calls[0].get("function", {})
        if function_data.get("name") != "classify_voice_command":
            return "Unknown", 0.0

        arguments = function_data.get("arguments", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        command = self.normalize_command(arguments.get("command", "Unknown"))

        try:
            confidence = float(arguments.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        return command, max(0.0, min(confidence, 1.0))

    def normalize_command(self, raw_command: Any) -> str:
        normalized = str(raw_command).strip().lower().replace("_", " ")
        command = {
            "stop": "Stop",
            "slow down": "Slow Down",
            "slowdown": "Slow Down",
            "proceed": "Proceed",
            "unknown": "Unknown",
        }.get(normalized, "Unknown")
        return command if command in self.VALID_COMMANDS else "Unknown"

    def publish_classification(
        self,
        text: str,
        start: str,
        command: str,
        confidence: float,
        error_message: Any = None,
    ) -> None:
        self.command_publisher.publish(String(data=command))
        self.confidence_publisher.publish(Float32(data=confidence))

        classification_message = {
            "text": text,
            "completed": True,
            "start": start,
            "command": command,
            "confidence": round(confidence, 4),
        }
        if error_message:
            classification_message["error"] = error_message

        self.classification_publisher.publish(
            String(data=json.dumps(classification_message, ensure_ascii=False))
        )

        print(
            "[CLASSIFICATION] command={} confidence={:.3f} text={}".format(
                command, confidence, text
            ),
            flush=True,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        rospy.loginfo(
            "Voice node: whisper=ws://%s:%d ollama_model=%s classification=%s "
            "same_output_threshold=%d silence=%d ms",
            self.whisper_host,
            self.whisper_port,
            self.ollama_model,
            self.enable_classification,
            self.same_output_threshold,
            self.min_silence_duration_ms,
        )
        for thread in self.threads:
            thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self.server_ready.clear()

        with self.socket_lock:
            ws = self.socket
        if ws is not None:
            try:
                ws.send(b"END_OF_AUDIO", opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception:
                pass
            try:
                ws.close()
            except Exception:
                pass

        try:
            if self.stream.is_active():
                self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass
        try:
            self.audio.terminate()
        except Exception:
            pass


def main() -> None:
    rospy.init_node("voice_command_node", anonymous=False)
    node = VoiceCommandNode()
    node.start()
    rospy.spin()


if __name__ == "__main__":
    main()
