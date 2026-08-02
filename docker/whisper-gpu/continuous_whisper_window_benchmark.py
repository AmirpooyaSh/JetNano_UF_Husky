#!/usr/bin/env python3
"""
Fixed-window continuous Whisper benchmark for Jetson.

Purpose
-------
- Capture continuously from a 48 kHz mono USB microphone.
- NEVER use VAD or wait for silence.
- Extract a fixed window (default 1.0 s) every fixed hop (default 0.1 s).
- Run Whisper persistently on CUDA.
- Measure inference time, queue wait, end-to-end window age, backlog, and drops.

Queue policies
--------------
latest:
    Practical low-latency mode. Keep only the newest pending window.
    Older unprocessed windows are replaced, preventing growing latency.

fifo:
    Stress-test mode. Queue every window until --max-queue is reached.
    This intentionally reveals whether the requested window/hop rate creates
    a processing bottleneck.

Examples
--------
Practical:
    python3 continuous_whisper_window_benchmark.py \
        --model base.en \
        --window 1.0 \
        --hop 0.1 \
        --queue-policy latest

Bottleneck stress test:
    python3 continuous_whisper_window_benchmark.py \
        --model base.en \
        --window 1.0 \
        --hop 0.1 \
        --queue-policy fifo \
        --max-queue 200
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

import numpy as np
import sounddevice as sd
import torch
import whisper
from scipy.signal import resample_poly


CAPTURE_RATE = 48_000
WHISPER_RATE = 16_000
CAPTURE_BLOCK_MS = 20
EPS = 1e-12


@dataclass(frozen=True)
class WindowJob:
    sequence: int
    audio_48k: np.ndarray
    window_ended_monotonic: float
    enqueued_monotonic: float


class SharedStats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.generated = 0
        self.processed = 0
        self.dropped = 0
        self.inference_times: Deque[float] = deque(maxlen=100)
        self.queue_wait_times: Deque[float] = deque(maxlen=100)
        self.total_ages: Deque[float] = deque(maxlen=100)

    def mark_generated(self) -> None:
        with self.lock:
            self.generated += 1

    def mark_dropped(self, count: int = 1) -> None:
        with self.lock:
            self.dropped += count

    def mark_processed(
        self,
        inference_seconds: float,
        queue_wait_seconds: float,
        total_age_seconds: float,
    ) -> None:
        with self.lock:
            self.processed += 1
            self.inference_times.append(inference_seconds)
            self.queue_wait_times.append(queue_wait_seconds)
            self.total_ages.append(total_age_seconds)

    def snapshot(self) -> dict:
        with self.lock:
            def average(values: Deque[float]) -> float:
                return float(np.mean(values)) if values else 0.0

            return {
                "generated": self.generated,
                "processed": self.processed,
                "dropped": self.dropped,
                "mean_inference_s": average(self.inference_times),
                "mean_queue_wait_s": average(self.queue_wait_times),
                "mean_total_age_s": average(self.total_ages),
            }


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


def enqueue_latest(
    work_queue: queue.Queue[WindowJob],
    job: WindowJob,
    stats: SharedStats,
) -> None:
    """
    Keep only the newest pending job. The job currently being processed is
    unaffected; only stale queued work is replaced.
    """
    try:
        work_queue.put_nowait(job)
        return
    except queue.Full:
        pass

    try:
        work_queue.get_nowait()
        work_queue.task_done()
        stats.mark_dropped()
    except queue.Empty:
        pass

    try:
        work_queue.put_nowait(job)
    except queue.Full:
        stats.mark_dropped()


def enqueue_fifo(
    work_queue: queue.Queue[WindowJob],
    job: WindowJob,
    stats: SharedStats,
) -> None:
    try:
        work_queue.put_nowait(job)
    except queue.Full:
        stats.mark_dropped()


def whisper_worker(
    args: argparse.Namespace,
    work_queue: queue.Queue[WindowJob],
    stop_event: threading.Event,
    model_ready: threading.Event,
    stats: SharedStats,
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

        started = time.perf_counter()
        queue_wait = started - job.enqueued_monotonic

        try:
            audio_16k = resample_poly(
                job.audio_48k,
                up=WHISPER_RATE,
                down=CAPTURE_RATE,
            ).astype(np.float32)

            torch.cuda.synchronize()
            inference_started = time.perf_counter()

            result = model.transcribe(
                audio_16k,
                language="en",
                task="transcribe",
                fp16=True,
                temperature=0.0,
                condition_on_previous_text=False,
                no_speech_threshold=args.no_speech_threshold,
                logprob_threshold=args.logprob_threshold,
                compression_ratio_threshold=args.compression_ratio_threshold,
                verbose=False,
            )

            torch.cuda.synchronize()
            completed = time.perf_counter()

            inference_seconds = completed - inference_started
            total_age = completed - job.window_ended_monotonic
            text = str(result.get("text", "")).strip()

            print(
                f"\n[LIVE TEXT #{job.sequence}] "
                f"{text if text else '<EMPTY>'}",
                flush=True,
            )

            stats.mark_processed(
                inference_seconds=inference_seconds,
                queue_wait_seconds=queue_wait,
                total_age_seconds=total_age,
            )

            output = {
                "sequence": job.sequence,
                "text": text,
                "inference_s": round(inference_seconds, 3),
                "queue_wait_s": round(queue_wait, 3),
                "window_age_at_result_s": round(total_age, 3),
                "queue_depth_after_get": work_queue.qsize(),
                "cuda_allocated_mib": round(
                    torch.cuda.memory_allocated() / 1024**2,
                    1,
                ),
                "cuda_reserved_mib": round(
                    torch.cuda.memory_reserved() / 1024**2,
                    1,
                ),
            }

            print("[RESULT] " + json.dumps(output, ensure_ascii=False))

        except Exception as exc:
            print(f"[Whisper] Window {job.sequence} failed: {exc}")
        finally:
            work_queue.task_done()


def stats_reporter(
    args: argparse.Namespace,
    work_queue: queue.Queue[WindowJob],
    stop_event: threading.Event,
    stats: SharedStats,
) -> None:
    while not stop_event.wait(args.stats_interval):
        snapshot = stats.snapshot()
        snapshot["queue_depth"] = work_queue.qsize()

        # Arrival load estimate:
        # windows generated per real second = 1 / hop.
        # Required serial GPU utilization ~= arrival_rate * mean inference.
        arrival_rate = 1.0 / args.hop
        snapshot["requested_windows_per_s"] = round(arrival_rate, 2)
        snapshot["estimated_gpu_load"] = round(
            arrival_rate * snapshot["mean_inference_s"],
            2,
        )

        print("\n[STATS] " + json.dumps(snapshot, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--device-name",
        default="AB13X USB Audio",
        help="Substring used to select the microphone.",
    )
    parser.add_argument("--model", default="base")
    parser.add_argument("--model-dir", default="/models")

    parser.add_argument(
        "--window",
        type=float,
        default=1.0,
        help="Audio window length in seconds.",
    )
    parser.add_argument(
        "--hop",
        type=float,
        default=0.1,
        help="Time between new windows in seconds.",
    )
    parser.add_argument(
        "--queue-policy",
        choices=("latest", "fifo"),
        default="latest",
    )
    parser.add_argument(
        "--max-queue",
        type=int,
        default=200,
        help="FIFO queue capacity; ignored by latest except internal size=1.",
    )
    parser.add_argument("--stats-interval", type=float, default=5.0)

    parser.add_argument("--no-speech-threshold", type=float, default=0.6)
    parser.add_argument("--logprob-threshold", type=float, default=-1.0)
    parser.add_argument(
        "--compression-ratio-threshold",
        type=float,
        default=2.4,
    )

    args = parser.parse_args()

    if args.window <= 0:
        parser.error("--window must be positive.")
    if args.hop <= 0:
        parser.error("--hop must be positive.")
    if args.hop > args.window:
        parser.error("--hop should not exceed --window for overlap.")
    if args.max_queue < 1:
        parser.error("--max-queue must be at least 1.")

    return args


def main() -> None:
    args = parse_args()

    microphone_index = find_microphone(args.device_name)

    block_samples = CAPTURE_RATE * CAPTURE_BLOCK_MS // 1000
    window_samples = int(round(CAPTURE_RATE * args.window))
    hop_samples = int(round(CAPTURE_RATE * args.hop))

    if window_samples < block_samples:
        raise ValueError("Window must be at least one capture block.")

    queue_size = 1 if args.queue_policy == "latest" else args.max_queue
    work_queue: queue.Queue[WindowJob] = queue.Queue(maxsize=queue_size)

    stop_event = threading.Event()
    model_ready = threading.Event()
    stats = SharedStats()

    worker = threading.Thread(
        target=whisper_worker,
        args=(args, work_queue, stop_event, model_ready, stats),
        daemon=True,
    )
    reporter = threading.Thread(
        target=stats_reporter,
        args=(args, work_queue, stop_event, stats),
        daemon=True,
    )

    worker.start()
    reporter.start()
    model_ready.wait()

    ring = np.zeros(window_samples, dtype=np.float32)
    write_position = 0
    samples_seen = 0
    samples_since_window = 0
    sequence = 0

    print(
        f"[Audio] Fixed-window mode: window={args.window:.3f}s, "
        f"hop={args.hop:.3f}s, policy={args.queue_policy}"
    )
    print("[Audio] No VAD is used. Press Ctrl+C to stop.")

    try:
        with sd.RawInputStream(
            device=microphone_index,
            samplerate=CAPTURE_RATE,
            blocksize=block_samples,
            channels=1,
            dtype="int16",
            latency="low",
        ) as stream:

            while True:
                frame, overflowed = stream.read(block_samples)
                if overflowed:
                    print("[Audio] Warning: input overflow.")

                pcm = np.frombuffer(bytes(frame), dtype=np.int16)
                audio = pcm.astype(np.float32) / 32768.0

                remaining = audio.size
                source_offset = 0

                while remaining > 0:
                    writable = min(remaining, window_samples - write_position)
                    ring[
                        write_position : write_position + writable
                    ] = audio[source_offset : source_offset + writable]

                    write_position = (write_position + writable) % window_samples
                    source_offset += writable
                    remaining -= writable

                samples_seen += audio.size
                samples_since_window += audio.size

                if samples_seen < window_samples:
                    continue

                while samples_since_window >= hop_samples:
                    samples_since_window -= hop_samples
                    sequence += 1
                    stats.mark_generated()

                    # Oldest sample starts at write_position after ring is full.
                    window = np.concatenate(
                        (ring[write_position:], ring[:write_position])
                    ).copy()

                    now = time.perf_counter()
                    job = WindowJob(
                        sequence=sequence,
                        audio_48k=window,
                        window_ended_monotonic=now,
                        enqueued_monotonic=now,
                    )

                    if args.queue_policy == "latest":
                        enqueue_latest(work_queue, job, stats)
                    else:
                        enqueue_fifo(work_queue, job, stats)

    except KeyboardInterrupt:
        print("\n[System] Stopping...")
    finally:
        stop_event.set()
        worker.join(timeout=10.0)
        reporter.join(timeout=1.0)

        final_stats = stats.snapshot()
        final_stats["queue_depth"] = work_queue.qsize()
        print("[FINAL STATS] " + json.dumps(final_stats, indent=2))


if __name__ == "__main__":
    main()
