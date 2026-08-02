#!/usr/bin/env python3
"""
Continuous microphone -> denoise -> VAD -> Whisper GPU test.

Designed for:
- NVIDIA Jetson
- 48 kHz mono USB microphone
- OpenAI Whisper "base" on CUDA
- Motor/chassis noise calibration while the robot is moving

IMPORTANT TEST PROCEDURE
------------------------
1. Start this script.
2. After Whisper loads, the script performs a short noise calibration.
3. During calibration, keep the robot moving but DO NOT speak.
4. After "[Audio] Listening..." appears, test commands at different distances.

The script:
- captures at 48 kHz,
- applies a streaming high-pass filter,
- uses calibrated noise-floor gating together with WebRTC VAD,
- applies conservative spectral noise suppression to completed utterances,
- resamples to 16 kHz,
- transcribes with Whisper on CUDA,
- optionally saves raw and cleaned utterance WAV files.

This is a test front end, not a safety-certified emergency-stop system.
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
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import torch
import webrtcvad
import whisper
from scipy.io import wavfile
from scipy.ndimage import uniform_filter
from scipy.signal import (
    butter,
    istft,
    resample_poly,
    sosfilt,
    sosfilt_zi,
    stft,
)


EPS = 1e-12
WHISPER_RATE = 16_000
FRAME_MS = 30

# Spectral suppression settings.
STFT_WINDOW_MS = 20
STFT_HOP_MS = 5


@dataclass(frozen=True)
class NoiseProfile:
    rms_dbfs: float
    spectral_magnitude: np.ndarray
    stft_nperseg: int
    stft_noverlap: int


@dataclass(frozen=True)
class AudioJob:
    audio_48k: np.ndarray
    captured_seconds: float
    noise_profile: NoiseProfile
    sequence: int


def dbfs(audio: np.ndarray) -> float:
    """Return RMS level in dBFS for float audio nominally in [-1, 1]."""
    if audio.size == 0:
        return -120.0

    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)) + EPS))
    return 20.0 * math.log10(max(rms, EPS))


def peak_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0

    peak = float(np.max(np.abs(audio)))
    return 20.0 * math.log10(max(peak, EPS))


def float_to_pcm16(audio: np.ndarray) -> np.ndarray:
    """Safely convert float audio to signed 16-bit PCM."""
    return np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)


def find_microphone(name_part: str) -> int:
    devices = sd.query_devices()
    matches: list[tuple[int, str]] = []

    for index, device in enumerate(devices):
        device_name = str(device["name"])
        if (
            int(device["max_input_channels"]) > 0
            and name_part.lower() in device_name.lower()
        ):
            matches.append((index, device_name))

    if not matches:
        print("[Audio] Available input devices:")
        for index, device in enumerate(devices):
            if int(device["max_input_channels"]) > 0:
                print(f"  {index}: {device['name']}")

        raise RuntimeError(
            f'No input device containing "{name_part}" was found.'
        )

    index, device_name = matches[0]
    print(f"[Audio] Selected device {index}: {device_name}")
    return index


class StreamingHighPass:
    """Stateful Butterworth high-pass filter for continuous frames."""

    def __init__(
        self,
        sample_rate: int,
        cutoff_hz: float,
        order: int = 4,
    ) -> None:
        if cutoff_hz <= 0 or cutoff_hz >= sample_rate / 2:
            raise ValueError("Invalid high-pass cutoff.")

        self.sos = butter(
            order,
            cutoff_hz,
            btype="highpass",
            fs=sample_rate,
            output="sos",
        )
        self.zi = sosfilt_zi(self.sos) * 0.0

    def process(self, audio: np.ndarray) -> np.ndarray:
        filtered, self.zi = sosfilt(self.sos, audio, zi=self.zi)
        return filtered.astype(np.float32, copy=False)


def create_noise_profile(
    noise_audio: np.ndarray,
    sample_rate: int,
) -> NoiseProfile:
    nperseg = int(sample_rate * STFT_WINDOW_MS / 1000)
    hop = int(sample_rate * STFT_HOP_MS / 1000)
    noverlap = nperseg - hop

    _, _, spectrum = stft(
        noise_audio,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        boundary=None,
        padded=False,
    )

    if spectrum.size == 0:
        raise RuntimeError("Noise calibration was too short.")

    # Median is less sensitive to occasional clicks or impacts during calibration.
    noise_magnitude = np.median(np.abs(spectrum), axis=1).astype(np.float32)

    return NoiseProfile(
        rms_dbfs=dbfs(noise_audio),
        spectral_magnitude=noise_magnitude,
        stft_nperseg=nperseg,
        stft_noverlap=noverlap,
    )


def suppress_noise(
    audio: np.ndarray,
    sample_rate: int,
    profile: NoiseProfile,
    strength: float,
    gain_floor: float,
) -> np.ndarray:
    """
    Conservative calibrated spectral subtraction.

    strength:
        Larger values remove more stationary noise but can damage speech.

    gain_floor:
        Minimum retained spectral amplitude. Prevents aggressive musical-noise
        artifacts and avoids completely deleting weak distant speech.
    """
    _, _, spectrum = stft(
        audio,
        fs=sample_rate,
        window="hann",
        nperseg=profile.stft_nperseg,
        noverlap=profile.stft_noverlap,
        boundary=None,
        padded=True,
    )

    magnitude = np.abs(spectrum)
    phase = np.exp(1j * np.angle(spectrum))

    noise = profile.spectral_magnitude[:, np.newaxis]
    gain = 1.0 - strength * noise / (magnitude + EPS)
    gain = np.clip(gain, gain_floor, 1.0)

    # Smooth the gain in time and frequency to reduce musical-noise artifacts.
    gain = uniform_filter(gain, size=(3, 3), mode="nearest")
    cleaned_spectrum = magnitude * gain * phase

    _, cleaned = istft(
        cleaned_spectrum,
        fs=sample_rate,
        window="hann",
        nperseg=profile.stft_nperseg,
        noverlap=profile.stft_noverlap,
        input_onesided=True,
        boundary=False,
    )

    if cleaned.size < audio.size:
        cleaned = np.pad(cleaned, (0, audio.size - cleaned.size))
    else:
        cleaned = cleaned[: audio.size]

    # Do not normalize upward: amplification would also amplify residual noise.
    peak = float(np.max(np.abs(cleaned))) if cleaned.size else 0.0
    if peak > 0.999:
        cleaned = cleaned / peak * 0.999

    return cleaned.astype(np.float32, copy=False)


def save_debug_audio(
    save_dir: Path,
    sequence: int,
    raw_48k: np.ndarray,
    cleaned_48k: np.ndarray,
    sample_rate: int,
) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    stem = f"{timestamp}_{sequence:04d}"

    wavfile.write(
        save_dir / f"{stem}_filtered_raw.wav",
        sample_rate,
        float_to_pcm16(raw_48k),
    )
    wavfile.write(
        save_dir / f"{stem}_cleaned.wav",
        sample_rate,
        float_to_pcm16(cleaned_48k),
    )


def transcription_worker(
    args: argparse.Namespace,
    audio_queue: queue.Queue[AudioJob],
    stop_event: threading.Event,
    model_ready: threading.Event,
) -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable inside the container.")

    print(f"[Whisper] Loading {args.model} model on CUDA...")
    model = whisper.load_model(
        args.model,
        device="cuda",
        download_root=args.model_dir,
    )
    torch.cuda.synchronize()

    print(f"[Whisper] Ready on {torch.cuda.get_device_name(0)}")
    model_ready.set()

    save_dir = Path(args.save_dir) if args.save_dir else None

    while not stop_event.is_set():
        try:
            job = audio_queue.get(timeout=0.2)
        except queue.Empty:
            continue

        try:
            processing_started = time.perf_counter()

            cleaned_48k = suppress_noise(
                audio=job.audio_48k,
                sample_rate=args.capture_rate,
                profile=job.noise_profile,
                strength=args.denoise_strength,
                gain_floor=args.denoise_floor,
            )

            cleaned_16k = resample_poly(
                cleaned_48k,
                up=WHISPER_RATE,
                down=args.capture_rate,
            ).astype(np.float32)

            if save_dir is not None:
                save_debug_audio(
                    save_dir=save_dir,
                    sequence=job.sequence,
                    raw_48k=job.audio_48k,
                    cleaned_48k=cleaned_48k,
                    sample_rate=args.capture_rate,
                )

            torch.cuda.synchronize()
            inference_started = time.perf_counter()

            result = model.transcribe(
                cleaned_16k,
                language="en",
                task="transcribe",
                fp16=True,
                temperature=0.0,
                condition_on_previous_text=False,
                verbose=False,
            )

            torch.cuda.synchronize()
            inference_seconds = time.perf_counter() - inference_started
            total_processing_seconds = time.perf_counter() - processing_started

            text = str(result.get("text", "")).strip()

            message = {
                "sequence": job.sequence,
                "text": text,
                "captured_seconds": round(job.captured_seconds, 3),
                "whisper_seconds": round(inference_seconds, 3),
                "total_processing_seconds": round(total_processing_seconds, 3),
                "raw_rms_dbfs": round(dbfs(job.audio_48k), 2),
                "cleaned_rms_dbfs": round(dbfs(cleaned_48k), 2),
                "cleaned_peak_dbfs": round(peak_dbfs(cleaned_48k), 2),
                "noise_floor_dbfs": round(job.noise_profile.rms_dbfs, 2),
                "timestamp": time.time(),
            }

            print("\n[TRANSCRIPTION]")
            print(json.dumps(message, indent=2))

        except Exception as exc:
            print(f"[Whisper] Failed to process chunk: {exc}")
        finally:
            audio_queue.task_done()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuous denoised Whisper GPU test."
    )

    parser.add_argument(
        "--device-name",
        default="AB13X USB Audio",
        help="Substring used to select the microphone.",
    )
    parser.add_argument("--capture-rate", type=int, default=48_000)
    parser.add_argument("--model", default="base")
    parser.add_argument("--model-dir", default="/models")

    parser.add_argument(
        "--calibration-seconds",
        type=float,
        default=4.0,
        help="Robot-noise calibration duration. Do not speak during it.",
    )
    parser.add_argument(
        "--highpass-hz",
        type=float,
        default=140.0,
        help="High-pass cutoff for vibration and motor rumble.",
    )

    parser.add_argument(
        "--vad-mode",
        type=int,
        choices=(0, 1, 2, 3),
        default=3,
        help="WebRTC VAD aggressiveness.",
    )
    parser.add_argument(
        "--snr-start-db",
        type=float,
        default=5.0,
        help="Required frame level above calibrated noise to start speech.",
    )
    parser.add_argument(
        "--snr-continue-db",
        type=float,
        default=2.0,
        help="Required frame level above calibrated noise during speech.",
    )
    parser.add_argument("--start-window-ms", type=int, default=300)
    parser.add_argument(
        "--start-ratio",
        type=float,
        default=0.50,
        help="Fraction of start-window frames that must pass both gates.",
    )
    parser.add_argument("--pre-roll-ms", type=int, default=450)
    parser.add_argument("--end-silence-ms", type=int, default=750)
    parser.add_argument("--tail-keep-ms", type=int, default=150)
    parser.add_argument("--min-utterance-seconds", type=float, default=0.45)
    parser.add_argument("--max-utterance-seconds", type=float, default=8.0)

    parser.add_argument(
        "--denoise-strength",
        type=float,
        default=1.25,
        help="Spectral subtraction strength; try 1.0-1.6.",
    )
    parser.add_argument(
        "--denoise-floor",
        type=float,
        default=0.15,
        help="Minimum retained spectral gain; try 0.10-0.25.",
    )
    parser.add_argument(
        "--save-dir",
        default="",
        help="Optional directory for filtered-raw and cleaned WAV chunks.",
    )

    args = parser.parse_args()

    if args.capture_rate not in (8_000, 16_000, 32_000, 48_000):
        parser.error("WebRTC VAD supports 8, 16, 32, or 48 kHz.")

    if not 0.0 < args.start_ratio <= 1.0:
        parser.error("--start-ratio must be in (0, 1].")

    if args.capture_rate % WHISPER_RATE != 0:
        parser.error(
            "This test expects capture rate to be an integer multiple of 16 kHz."
        )

    return args


def main() -> None:
    args = parse_args()

    frame_samples = args.capture_rate * FRAME_MS // 1000
    calibration_frames = max(
        1,
        int(args.calibration_seconds * 1000 / FRAME_MS),
    )
    pre_roll_frames = max(1, int(args.pre_roll_ms / FRAME_MS))
    start_window_frames = max(1, int(args.start_window_ms / FRAME_MS))
    start_required_frames = max(
        1,
        math.ceil(start_window_frames * args.start_ratio),
    )
    end_silence_frames = max(1, int(args.end_silence_ms / FRAME_MS))
    tail_keep_frames = max(0, int(args.tail_keep_ms / FRAME_MS))
    max_utterance_frames = max(
        1,
        int(args.max_utterance_seconds * 1000 / FRAME_MS),
    )

    microphone_index = find_microphone(args.device_name)
    high_pass = StreamingHighPass(
        sample_rate=args.capture_rate,
        cutoff_hz=args.highpass_hz,
    )
    vad = webrtcvad.Vad(args.vad_mode)

    audio_queue: queue.Queue[AudioJob] = queue.Queue(maxsize=3)
    stop_event = threading.Event()
    model_ready = threading.Event()

    worker = threading.Thread(
        target=transcription_worker,
        args=(args, audio_queue, stop_event, model_ready),
        daemon=True,
    )
    worker.start()

    model_ready.wait()

    print(
        "\n[Calibration] Keep the robot moving and remain silent for "
        f"{args.calibration_seconds:.1f} seconds."
    )

    calibration_audio: list[np.ndarray] = []

    pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_frames)
    start_window: deque[bool] = deque(maxlen=start_window_frames)

    speech_frames: list[np.ndarray] = []
    speech_active = False
    silence_frames = 0
    sequence = 0
    noise_profile: Optional[NoiseProfile] = None

    try:
        with sd.RawInputStream(
            device=microphone_index,
            samplerate=args.capture_rate,
            blocksize=frame_samples,
            channels=1,
            dtype="int16",
            latency="low",
        ) as stream:

            for frame_number in range(calibration_frames):
                frame, overflowed = stream.read(frame_samples)
                if overflowed:
                    print("[Audio] Warning: input overflow during calibration.")

                raw = np.frombuffer(bytes(frame), dtype=np.int16)
                floating = raw.astype(np.float32) / 32768.0
                filtered = high_pass.process(floating)
                calibration_audio.append(filtered.copy())

                completed = frame_number + 1
                frames_per_second = max(1, int(1000 / FRAME_MS))
                if completed % frames_per_second == 0:
                    remaining = max(
                        0.0,
                        args.calibration_seconds
                        - completed * FRAME_MS / 1000.0,
                    )
                    print(
                        f"[Calibration] {remaining:.1f} seconds remaining..."
                    )

            calibration_array = np.concatenate(calibration_audio)
            noise_profile = create_noise_profile(
                noise_audio=calibration_array,
                sample_rate=args.capture_rate,
            )

            print(
                "[Calibration] Complete. "
                f"Noise floor: {noise_profile.rms_dbfs:.2f} dBFS"
            )
            print(
                "[Audio] Listening continuously. "
                "Press Ctrl+C to stop.\n"
            )

            while not stop_event.is_set():
                frame, overflowed = stream.read(frame_samples)
                if overflowed:
                    print("[Audio] Warning: input overflow.")

                raw = np.frombuffer(bytes(frame), dtype=np.int16)
                floating = raw.astype(np.float32) / 32768.0
                filtered = high_pass.process(floating)

                vad_pcm = float_to_pcm16(filtered).tobytes()
                vad_speech = vad.is_speech(vad_pcm, args.capture_rate)
                frame_level = dbfs(filtered)

                start_gate = (
                    vad_speech
                    and frame_level
                    >= noise_profile.rms_dbfs + args.snr_start_db
                )
                continue_gate = (
                    vad_speech
                    and frame_level
                    >= noise_profile.rms_dbfs + args.snr_continue_db
                )

                if not speech_active:
                    pre_roll.append(filtered.copy())
                    start_window.append(start_gate)

                    if (
                        len(start_window) == start_window_frames
                        and sum(start_window) >= start_required_frames
                    ):
                        speech_active = True
                        speech_frames = [frame.copy() for frame in pre_roll]
                        silence_frames = 0

                        print(
                            "[Audio] Speech started "
                            f"(frame={frame_level:.1f} dBFS, "
                            f"noise={noise_profile.rms_dbfs:.1f} dBFS)."
                        )

                else:
                    speech_frames.append(filtered.copy())

                    if continue_gate:
                        silence_frames = 0
                    else:
                        silence_frames += 1

                    end_due_to_silence = (
                        silence_frames >= end_silence_frames
                    )
                    end_due_to_length = (
                        len(speech_frames) >= max_utterance_frames
                    )

                    if end_due_to_silence or end_due_to_length:
                        if end_due_to_silence:
                            removable = max(
                                0,
                                silence_frames - tail_keep_frames,
                            )
                            if removable > 0:
                                speech_frames = speech_frames[:-removable]

                        utterance = np.concatenate(speech_frames)
                        duration = utterance.size / args.capture_rate

                        if duration >= args.min_utterance_seconds:
                            sequence += 1
                            print(
                                "[Audio] Speech ended: "
                                f"{duration:.2f} seconds "
                                f"(chunk {sequence})."
                            )

                            job = AudioJob(
                                audio_48k=utterance,
                                captured_seconds=duration,
                                noise_profile=noise_profile,
                                sequence=sequence,
                            )

                            try:
                                audio_queue.put_nowait(job)
                            except queue.Full:
                                print(
                                    "[Audio] Transcription queue full; "
                                    "dropping this chunk."
                                )
                        else:
                            print(
                                "[Audio] Ignored short activation: "
                                f"{duration:.2f} seconds."
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
        worker.join(timeout=3.0)


if __name__ == "__main__":
    main()
