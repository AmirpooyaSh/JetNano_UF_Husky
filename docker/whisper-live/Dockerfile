# WhisperLive server for Jetson Orin (JetPack 6.2.2 / L4T R36.5)
#
# Base: the existing whisper-jetson:base-en image
#   Ubuntu 24.04, Python 3.12, NVIDIA torch 2.6 (CUDA 12.6, cuDNN 9.6)
#
# Adds:
#   - CTranslate2 compiled with CUDA + cuDNN for Orin (sm_87)
#   - faster-whisper 1.1.0
#   - WhisperLive (server only, faster_whisper backend)

ARG BASE_IMAGE=whisper-jetson:base-en
FROM ${BASE_IMAGE}

ARG CTRANSLATE2_VERSION=4.5.0
ARG WHISPERLIVE_VERSION=v0.7.1
ARG CUDA_ARCH=8.7
ARG BUILD_JOBS=4

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Keep NVIDIA's torch and numpy exactly as they are in the base image.
RUN python3 -c "import torch, numpy; print(f'torch=={torch.__version__}'); print(f'numpy=={numpy.__version__}')" \
        > /opt/constraints.txt && \
    cat /opt/constraints.txt

# -----------------------------------------------------------------------------
# CTranslate2 with CUDA (the PyPI aarch64 wheel is CPU-only)
# -----------------------------------------------------------------------------
RUN git clone --depth 1 --recursive --branch "v${CTRANSLATE2_VERSION}" \
        https://github.com/OpenNMT/CTranslate2.git /opt/CTranslate2 && \
    cmake -S /opt/CTranslate2 -B /opt/CTranslate2/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DWITH_CUDA=ON \
        -DWITH_CUDNN=ON \
        -DWITH_MKL=OFF \
        -DWITH_RUY=ON \
        -DOPENMP_RUNTIME=COMP \
        -DCUDA_ARCH_LIST="${CUDA_ARCH}" \
        -DBUILD_CLI=OFF && \
    cmake --build /opt/CTranslate2/build -j"${BUILD_JOBS}" && \
    cmake --install /opt/CTranslate2/build && \
    ldconfig && \
    cd /opt/CTranslate2/python && \
    pip3 install -c /opt/constraints.txt -r install_requirements.txt && \
    pip3 install -c /opt/constraints.txt --no-build-isolation . && \
    rm -rf /opt/CTranslate2/build

# -----------------------------------------------------------------------------
# faster-whisper + WhisperLive server dependencies
# (--no-deps so pip cannot replace NVIDIA torch/numpy)
# -----------------------------------------------------------------------------
RUN pip3 install -c /opt/constraints.txt \
        "av>=11" \
        "tokenizers==0.20.3" \
        "huggingface_hub>=0.23,<1.0" \
        "onnxruntime>=1.17,<2" \
        "websockets>=12,<16" \
        tqdm \
    && pip3 install -c /opt/constraints.txt --no-deps "faster-whisper==1.1.0"

RUN git clone --depth 1 --branch "${WHISPERLIVE_VERSION}" \
        https://github.com/collabora/WhisperLive.git /opt/WhisperLive && \
    pip3 install -c /opt/constraints.txt --no-deps /opt/WhisperLive

# Fail the build now if anything the server imports is missing.
RUN python3 -c "import ctranslate2, faster_whisper, whisper_live.server; \
from whisper_live.backend.faster_whisper_backend import ServeClientFasterWhisper; \
print('ctranslate2', ctranslate2.__version__, 'faster_whisper', faster_whisper.__version__)"

COPY start_server.py /opt/start_server.py

ENV WHISPER_MODEL=base.en \
    WHISPER_PORT=9090 \
    WHISPER_CACHE_DIR=/models \
    HF_HOME=/models/huggingface

EXPOSE 9090

CMD ["python3", "/opt/start_server.py"]
