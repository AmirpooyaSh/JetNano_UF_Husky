#!/usr/bin/env python3
"""
Jetson microphone -> WebRTC VAD -> Whisper base on CUDA.

The VAD is tuned for a shirt-mounted microphone and is used only to determine when an utterance starts and ends.
Whisper receives one complete utterance rather than fixed overlapping windows.

Output:
    0.542 s | Stop right there.

The reported time is measured from the detected end of speech until the
transcription is returned.
"""

from __future__ import annotations

import argparse
import math
import queue
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

import numpy as np
import sounddevice as sd
import torch
import webrtcvad
import whisper
from scipy.signal import resample_poly


WHISPER_RATE = 16_000
FRAME_MS = 30


@dataclass(frozen=True)
class UtteranceJob:
    audio_48k: np.ndarray
    speech_end_time: float


def find_microphone(name_part: str) -> int:
    matches: list[tuple[int, str]] = []

    for index, device in enumerate(sd.query_devices()):
        name = str(device["name"])
        if (
            int(device["max_input_channels"]) > 0
            and name_part.lower() in name.lower()
        ):
            matches.append((index, name))

    if not matches:
        print("[Audio] Available input devices:")
        for index, device in enumerate(sd.query_devices()):
            if int(device["max_input_channels"]) > 0:
                print(f"  {index}: {device['name']}")

        raise RuntimeError(
            f'No input device containing "{name_part}" was found.'
        )

    index, name = matches[0]
    print(f"[Audio] Selected device {index}: {name}")
    return index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--device-name",
        default="AB13X USB Audio",
    )
    parser.add_argument("--capture-rate", type=int, default=48_000)
    parser.add_argument("--model", default="base")
    parser.add_argument("--model-dir", default="/models")
    parser.add_argument("--language", default="en")

    parser.add_argument(
        "--vad-mode",
        type=int,
        choices=(0, 1, 2, 3),
        default=3,
        help="0 is least aggressive; 3 is most aggressive.",
    )
    parser.add_argument("--pre-roll-ms", type=int, default=180)
    parser.add_argument("--start-window-ms", type=int, default=150)
    parser.add_argument(
        "--start-ratio",
        type=float,
        default=0.80,
        help="Required voiced fraction in the start window.",
    )
    parser.add_argument("--end-silence-ms", type=int, default=300)
    parser.add_argument("--tail-keep-ms", type=int, default=90)
    parser.add_argument("--min-speech-seconds", type=float, default=0.20)
    parser.add_argument("--max-speech-seconds", type=float, default=1.50)

    args = parser.parse_args()

    if args.capture_rate not in (8_000, 16_000, 32_000, 48_000):
        parser.error(
            "WebRTC VAD supports 8000, 16000, 32000, or 48000 Hz."
        )

    if not 0.0 < args.start_ratio <= 1.0:
        parser.error("--start-ratio must be in (0, 1].")

    return args


def transcription_worker(
    args: argparse.Namespace,
    work_queue: queue.Queue[UtteranceJob],
    stop_event: threading.Event,
    model_ready: threading.Event,
) -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable inside the container.")

    print(f"[Whisper] Loading {args.model} on CUDA...")

    model = whisper.load_model(
        args.model,
        device="cuda",
        download_root=args.model_dir,
    )

    torch.cuda.synchronize()
    print(f"[Whisper] Ready on {torch.cuda.get_device_name(0)}")
    model_ready.set()

    while not stop_event.is_set() or not work_queue.empty():
        try:
            job = work_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        try:
            if args.capture_rate == WHISPER_RATE:
                audio_16k = job.audio_48k.astype(
                    np.float32,
                    copy=False,
                )
            else:
                audio_16k = resample_poly(
                    job.audio_48k,
                    up=WHISPER_RATE,
                    down=args.capture_rate,
                ).astype(np.float32)

            result = model.transcribe(
                audio_16k,
                language=args.language,
                task="transcribe",
                fp16=True,
                temperature=0.0,
                condition_on_previous_text=False,
                verbose=False,
            )

            torch.cuda.synchronize()

            text = str(result.get("text", "")).strip()
            latency = time.perf_counter() - job.speech_end_time

            if text:
                print(f"{latency:.3f} s | {text}", flush=True)

        except Exception as exc:
            print(f"ERROR | {exc}", flush=True)
        finally:
            work_queue.task_done()


def main() -> int:
    args = parse_args()

    microphone_index = find_microphone(args.device_name)

    frame_samples = args.capture_rate * FRAME_MS // 1000
    pre_roll_frames = max(1, math.ceil(args.pre_roll_ms / FRAME_MS))
    start_window_frames = max(
        1,
        math.ceil(args.start_window_ms / FRAME_MS),
    )
    start_required_frames = max(
        1,
        math.ceil(start_window_frames * args.start_ratio),
    )
    end_silence_frames = max(
        1,
        math.ceil(args.end_silence_ms / FRAME_MS),
    )
    tail_keep_frames = max(
        0,
        math.ceil(args.tail_keep_ms / FRAME_MS),
    )
    max_speech_frames = max(
        1,
        math.ceil(args.max_speech_seconds * 1000 / FRAME_MS),
    )

    vad = webrtcvad.Vad(args.vad_mode)

    work_queue: queue.Queue[UtteranceJob] = queue.Queue(maxsize=2)
    stop_event = threading.Event()
    model_ready = threading.Event()

    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    worker = threading.Thread(
        target=transcription_worker,
        args=(args, work_queue, stop_event, model_ready),
        daemon=True,
    )
    worker.start()
    model_ready.wait()

    pre_roll: Deque[bytes] = deque(maxlen=pre_roll_frames)
    start_window: Deque[bool] = deque(maxlen=start_window_frames)

    speech_active = False
    speech_frames: list[bytes] = []
    silence_frames = 0

    print(
        "[Audio] Shirt-mounted microphone VAD listening. Pause briefly between commands."
    )
    print("[Audio] Press Ctrl+C to stop.")

    try:
        with sd.RawInputStream(
            device=microphone_index,
            samplerate=args.capture_rate,
            blocksize=frame_samples,
            channels=1,
            dtype="int16",
            latency="low",
        ) as stream:

            while not stop_event.is_set():
                frame, overflowed = stream.read(frame_samples)
                frame_bytes = bytes(frame)

                if overflowed:
                    print("[Audio] Warning: input overflow.")

                voiced = vad.is_speech(
                    frame_bytes,
                    args.capture_rate,
                )

                if not speech_active:
                    pre_roll.append(frame_bytes)
                    start_window.append(voiced)

                    if (
                        len(start_window) == start_window_frames
                        and sum(start_window) >= start_required_frames
                    ):
                        speech_active = True
                        speech_frames = list(pre_roll)
                        silence_frames = 0
                        print("[Speech started]", flush=True)

                else:
                    speech_frames.append(frame_bytes)

                    if voiced:
                        silence_frames = 0
                    else:
                        silence_frames += 1

                    end_by_silence = (
                        silence_frames >= end_silence_frames
                    )
                    end_by_length = (
                        len(speech_frames) >= max_speech_frames
                    )

                    if not (end_by_silence or end_by_length):
                        continue

                    if end_by_silence:
                        removable = max(
                            0,
                            silence_frames - tail_keep_frames,
                        )
                        if removable > 0:
                            speech_frames = speech_frames[:-removable]

                    pcm = b"".join(speech_frames)
                    audio = (
                        np.frombuffer(pcm, dtype=np.int16)
                        .astype(np.float32)
                        / 32768.0
                    )

                    duration = audio.size / args.capture_rate
                    speech_end_time = time.perf_counter()

                    if duration >= args.min_speech_seconds:
                        print(
                            f"[Speech ended: {duration:.2f} s]",
                            flush=True,
                        )

                        job = UtteranceJob(
                            audio_48k=audio,
                            speech_end_time=speech_end_time,
                        )

                        try:
                            work_queue.put_nowait(job)
                        except queue.Full:
                            print(
                                "[Audio] Transcription queue full; "
                                "utterance dropped."
                            )

                    speech_active = False
                    speech_frames = []
                    silence_frames = 0
                    pre_roll.clear()
                    start_window.clear()

    finally:
        stop_event.set()
        worker.join(timeout=10.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
