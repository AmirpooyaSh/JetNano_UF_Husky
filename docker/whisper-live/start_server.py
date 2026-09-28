#!/usr/bin/env python3
"""
Start the WhisperLive server with one preloaded model.

The model is chosen by the WHISPER_MODEL environment variable (set in run.sh).
It is downloaded to WHISPER_CACHE_DIR if needed, loaded on the GPU, warmed up,
and shared by every client. The port only opens after the model is ready.
"""

import logging
import os
import time

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

MODEL = os.environ.get("WHISPER_MODEL", "base.en")
PORT = int(os.environ.get("WHISPER_PORT", "9090"))
CACHE_DIR = os.environ.get("WHISPER_CACHE_DIR", "/models")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import ctranslate2  # noqa: E402
import torch  # noqa: E402
from faster_whisper.utils import download_model  # noqa: E402
from whisper_live.backend.faster_whisper_backend import (  # noqa: E402
    ServeClientFasterWhisper,
)
from whisper_live.server import TranscriptionServer  # noqa: E402
from whisper_live.transcriber.transcriber_faster_whisper import (  # noqa: E402
    WhisperModel,
)


def main() -> None:
    if os.path.isdir(MODEL):
        model_path = MODEL
    else:
        logging.info("Resolving model '%s' (cache: %s)...", MODEL, CACHE_DIR)
        model_path = download_model(MODEL, cache_dir=CACHE_DIR)
    logging.info("Model path: %s", model_path)

    torch_cuda = torch.cuda.is_available()
    ct2_cuda_devices = ctranslate2.get_cuda_device_count()
    logging.info(
        "torch CUDA=%s, CTranslate2 CUDA devices=%d",
        torch_cuda,
        ct2_cuda_devices,
    )

    if torch_cuda and ct2_cuda_devices > 0:
        device = "cuda"
        major, _ = torch.cuda.get_device_capability(0)
        compute_type = "float16" if major >= 7 else "float32"
    else:
        device = "cpu"
        compute_type = "int8"
        logging.warning("CUDA not available to both torch and CTranslate2. Using CPU.")

    logging.info("Loading %s on %s (%s)...", MODEL, device, compute_type)
    start = time.monotonic()
    model = WhisperModel(
        model_path,
        device=device,
        compute_type=compute_type,
        local_files_only=True,
    )
    logging.info("Model loaded in %.1f s.", time.monotonic() - start)

    start = time.monotonic()
    segments, _ = model.transcribe(
        np.zeros(16000, dtype=np.float32),
        language="en",
    )
    list(segments)
    logging.info("Warm-up finished in %.1f s.", time.monotonic() - start)

    # Every client connection reuses this already-loaded model.
    ServeClientFasterWhisper.SINGLE_MODEL = model

    logging.info("WHISPERLIVE_READY model=%s port=%d device=%s", MODEL, PORT, device)

    TranscriptionServer().run(
        "0.0.0.0",
        port=PORT,
        backend="faster_whisper",
        faster_whisper_custom_model_path=model_path,
        single_model=True,
    )


if __name__ == "__main__":
    main()
