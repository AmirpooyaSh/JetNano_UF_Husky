#!/usr/bin/env python3

import argparse
import statistics
import time

import torch
import whisper


def synchronize() -> None:
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available.")

    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print("Loading Whisper base...")

    start = time.perf_counter()
    model = whisper.load_model(
        "base",
        device="cuda",
        download_root="/models",
    )
    synchronize()

    print(f"Model load time: {time.perf_counter() - start:.3f} seconds")

    options = {
        "language": "en",
        "task": "transcribe",
        "fp16": True,
        "temperature": 0.0,
        "condition_on_previous_text": False,
    }

    print("\nCUDA warm-up...")
    model.transcribe(args.audio_file, **options)
    synchronize()

    times = []

    for run_number in range(1, args.runs + 1):
        synchronize()
        start = time.perf_counter()

        result = model.transcribe(args.audio_file, **options)

        synchronize()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

        print(
            f"Run {run_number}: {elapsed:.3f} seconds | "
            f"Text: {result['text'].strip()}"
        )

    print("\nResults")
    print(f"Average: {statistics.mean(times):.3f} seconds")
    print(f"Minimum: {min(times):.3f} seconds")
    print(f"Maximum: {max(times):.3f} seconds")

    print(
        f"CUDA memory allocated: "
        f"{torch.cuda.memory_allocated() / 1024**2:.1f} MiB"
    )
    print(
        f"CUDA memory reserved: "
        f"{torch.cuda.memory_reserved() / 1024**2:.1f} MiB"
    )


if __name__ == "__main__":
    main()
