#!/usr/bin/env python3

import argparse
import time

import torch
import whisper


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file")
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available inside the container.")

    print(f"PyTorch: {torch.__version__}")
    print(f"CUDA: {torch.version.cuda}")
    print(f"GPU: {torch.cuda.get_device_name(0)}")

    print("\nLoading Whisper base on CUDA...")
    load_start = time.perf_counter()

    model = whisper.load_model(
        "base",
        device="cuda",
        download_root="/models",
    )

    print(f"Model load time: {time.perf_counter() - load_start:.3f} seconds")

    inference_start = time.perf_counter()

    result = model.transcribe(
        args.audio_file,
        language="en",
        task="transcribe",
        fp16=True,
        temperature=0.0,
    )

    inference_time = time.perf_counter() - inference_start

    print("\nTranscription:")
    print(result["text"].strip())
    print(f"\nInference time: {inference_time:.3f} seconds")


if __name__ == "__main__":
    main()
