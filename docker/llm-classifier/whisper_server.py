#!/usr/bin/env python3

import os
import tempfile

import torch
import whisper
from flask import Flask, jsonify, request


MODEL_NAME = os.environ.get("WHISPER_MODEL", "base.en")
DEVICE = os.environ.get(
    "WHISPER_DEVICE",
    "cuda" if torch.cuda.is_available() else "cpu",
)

print("Loading Whisper model:", MODEL_NAME)
print("Device:", DEVICE)

model = whisper.load_model(MODEL_NAME, device=DEVICE)

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ready",
        "model": MODEL_NAME,
        "device": DEVICE,
    })


@app.route("/v1/audio/transcriptions", methods=["POST"])
def transcription():
    if "file" not in request.files:
        return jsonify({"error": "No audio file supplied"}), 400

    audio = request.files["file"]
    suffix = os.path.splitext(audio.filename)[1] or ".wav"

    temporary_file = tempfile.NamedTemporaryFile(
        suffix=suffix,
        delete=False,
    )
    temporary_path = temporary_file.name
    temporary_file.close()

    try:
        audio.save(temporary_path)

        result = model.transcribe(
            temporary_path,
            language=request.form.get("language", "en"),
            fp16=(DEVICE == "cuda"),
        )

        return jsonify({
            "text": result["text"].strip(),
        })

    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=9000,
        threaded=False,
    )
