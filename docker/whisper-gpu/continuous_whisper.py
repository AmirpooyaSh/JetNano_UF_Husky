#!/usr/bin/env python3

import json
import queue
import threading
import time
from collections import deque
from typing import Optional

import numpy as np
import sounddevice as sd
import torch
import webrtcvad
import whisper

from scipy.signal import resample_poly


CAPTURE_RATE = 48_000
WHISPER_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = CAPTURE_RATE * FRAME_MS // 1000

DEVICE_NAME_PART = "AB13X USB Audio"

VAD_MODE = 2
PRE_ROLL_FRAMES = 10          # 300 ms
START_WINDOW_FRAMES = 10      # 300 ms
START_VOICED_RATIO = 0.6
END_SILENCE_FRAMES = 20       # 600 ms
MAX_UTTERANCE_SECONDS = 8.0

audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=4)
stop_event = threading.Event()
model_ready = threading.Event()


def find_microphone(name_part: str) -> int:
    devices = sd.query_devices()

    for index, device in enumerate(devices):
        if (
            device["max_input_channels"] > 0
            and name_part.lower() in device["name"].lower()
        ):
            print(
                f"[Audio] Selected device {index}: "
                f"{device['name']}"
            )
            return index

    print("[Audio] Available input devices:")

    for index, device in enumerate(devices):
        if device["max_input_channels"] > 0:
            print(f"  {index}: {device['name']}")

    raise RuntimeError(
        f'Input device containing "{name_part}" was not found.'
    )


def transcription_worker() -> None:
    print("[Whisper] Loading base model on CUDA...")

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable inside the container.")

    model = whisper.load_model(
        "base",
        device="cuda",
        download_root="/models",
    )

    torch.cuda.synchronize()
    print(f"[Whisper] Ready on {torch.cuda.get_device_name(0)}")
    model_ready.set()

    while not stop_event.is_set():
        try:
            audio = audio_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        started = time.perf_counter()

        result = model.transcribe(
            audio,
            language="en",
            task="transcribe",
            fp16=True,
            temperature=0.0,
            condition_on_previous_text=False,
        )

        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        text = result.get("text", "").strip()

        message = {
            "text": text,
            "transcription_seconds": round(elapsed, 3),
            "audio_seconds": round(len(audio) / WHISPER_RATE, 3),
            "timestamp": time.time(),
        }

        print("\n[TRANSCRIPTION]")
        print(json.dumps(message, indent=2))

        audio_queue.task_done()


def main() -> None:
    microphone_index = find_microphone(DEVICE_NAME_PART)
    vad = webrtcvad.Vad(VAD_MODE)

    worker = threading.Thread(
        target=transcription_worker,
        daemon=True,
    )
    worker.start()

    model_ready.wait()

    pre_roll: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
    start_window: deque[bool] = deque(maxlen=START_WINDOW_FRAMES)

    speech_frames: list[bytes] = []
    speech_active = False
    silence_frames = 0

    max_frames = int(
        MAX_UTTERANCE_SECONDS * 1000 / FRAME_MS
    )

    print("[Audio] Listening continuously. Press Ctrl+C to stop.")

    try:
        with sd.RawInputStream(
            device=microphone_index,
            samplerate=CAPTURE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
        ) as stream:

            while not stop_event.is_set():
                frame, overflowed = stream.read(FRAME_SAMPLES)
                frame_bytes = bytes(frame)

                if overflowed:
                    print("[Audio] Warning: input overflow.")

                voiced = vad.is_speech(frame_bytes, CAPTURE_RATE)

                if not speech_active:
                    pre_roll.append(frame_bytes)
                    start_window.append(voiced)

                    voiced_ratio = (
                        sum(start_window) / len(start_window)
                        if start_window
                        else 0.0
                    )

                    if (
                        len(start_window) == START_WINDOW_FRAMES
                        and voiced_ratio >= START_VOICED_RATIO
                    ):
                        speech_active = True
                        speech_frames = list(pre_roll)
                        silence_frames = 0
                        print("[Audio] Speech started...")

                else:
                    speech_frames.append(frame_bytes)

                    if voiced:
                        silence_frames = 0
                    else:
                        silence_frames += 1

                    end_due_to_silence = (
                        silence_frames >= END_SILENCE_FRAMES
                    )
                    end_due_to_length = (
                        len(speech_frames) >= max_frames
                    )

                    if end_due_to_silence or end_due_to_length:
                        pcm = b"".join(speech_frames)

                        audio_48k = (
                            np.frombuffer(pcm, dtype=np.int16)
                            .astype(np.float32)
                            / 32768.0
                        )

                        # Convert the microphone's native 48 kHz audio to Whisper's 16 kHz input.
                        audio = resample_poly(
                            audio_48k,
                            up=WHISPER_RATE,
                            down=CAPTURE_RATE,
                        ).astype(np.float32)

                        duration = len(audio_48k) / CAPTURE_RATE
                        print(
                            f"[Audio] Speech ended: "
                            f"{duration:.2f} seconds"
                        )

                        try:
                            audio_queue.put_nowait(audio)
                        except queue.Full:
                            print(
                                "[Audio] Transcription queue full; "
                                "dropping this chunk."
                            )

                        speech_active = False
                        speech_frames = []
                        silence_frames = 0
                        pre_roll.clear()
                        start_window.clear()

    except KeyboardInterrupt:
        print("\n[System] Stopping...")
    finally:
        stop_event.set()
        worker.join(timeout=2.0)


if __name__ == "__main__":
    main()
