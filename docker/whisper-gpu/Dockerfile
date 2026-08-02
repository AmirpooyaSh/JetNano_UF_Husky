FROM nvcr.io/nvidia/pytorch:24.10-py3-igpu

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    alsa-utils \
    libasound2 \
    libportaudio2 \
    portaudio19-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install \
    git+https://github.com/openai/whisper.git \
    sounddevice \
    webrtcvad

WORKDIR /app