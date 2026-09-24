FROM nvcr.io/nvidia/pytorch:24.12-py3-igpu

ENV DEBIAN_FRONTEND=noninteractive
ENV WHISPER_MODEL=base.en
ENV WHISPER_DEVICE=cuda

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    openai-whisper \
    flask

WORKDIR /app

COPY whisper_server.py /app/whisper_server.py

EXPOSE 9000

CMD ["python3", "/app/whisper_server.py"]
