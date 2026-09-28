#!/usr/bin/env bash
# Generate the 6 announcement WAV files with Piper (offline text-to-speech).
#
# Run once on the Jetson host (needs internet the first time to download
# Piper and the voice). Output goes into robot_bringup/sounds/.
#
#   ./generate_announcements.sh
#   VOICE=en_US-amy-medium ./generate_announcements.sh    # different voice
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-${SCRIPT_DIR}/../sounds}"
CACHE_DIR="${CACHE_DIR:-$HOME/.cache/piper}"
VOICE="${VOICE:-en_US-lessac-medium}"
PIPER_RELEASE="2023.11.14-2"

case "$(uname -m)" in
    x86_64)  PIPER_ARCH="x86_64" ;;
    aarch64) PIPER_ARCH="aarch64" ;;
    *) echo "ERROR: unsupported architecture $(uname -m)"; exit 1 ;;
esac

mkdir -p "${OUT_DIR}" "${CACHE_DIR}"

# ---------------------------------------------------------------------------
# Download Piper and the voice (only the first time)
# ---------------------------------------------------------------------------
if [ ! -x "${CACHE_DIR}/piper/piper" ]; then
    echo "Downloading Piper ${PIPER_RELEASE} (${PIPER_ARCH})..."
    curl -fL -o "${CACHE_DIR}/piper.tar.gz" \
        "https://github.com/rhasspy/piper/releases/download/${PIPER_RELEASE}/piper_linux_${PIPER_ARCH}.tar.gz"
    tar -xzf "${CACHE_DIR}/piper.tar.gz" -C "${CACHE_DIR}"
    rm -f "${CACHE_DIR}/piper.tar.gz"
fi

# Voice name format: <lang>_<REGION>-<name>-<quality>
LANG_REGION="${VOICE%%-*}"
LANG_CODE="${LANG_REGION%%_*}"
VOICE_NAME="$(echo "${VOICE}" | cut -d- -f2)"
VOICE_QUALITY="$(echo "${VOICE}" | cut -d- -f3)"
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/${LANG_CODE}/${LANG_REGION}/${VOICE_NAME}/${VOICE_QUALITY}/${VOICE}"

for extension in onnx onnx.json; do
    if [ ! -f "${CACHE_DIR}/${VOICE}.${extension}" ]; then
        echo "Downloading voice ${VOICE}.${extension}..."
        curl -fL -o "${CACHE_DIR}/${VOICE}.${extension}" "${VOICE_URL}.${extension}"
    fi
done

# ---------------------------------------------------------------------------
# The 6 announcements
# ---------------------------------------------------------------------------
declare -A TEXT=(
    [stop_accepted]="Command Stop accepted."
    [slow_down_accepted]="Command Slow Down accepted."
    [proceed_accepted]="Command Proceed accepted."
    [stop_rejected]="Command Stop received, but rejected due to low confidence at this distance."
    [slow_down_rejected]="Command Slow Down received, but rejected due to low confidence at this distance."
    [proceed_rejected]="Command Proceed received, but rejected due to low confidence at this distance."
)

export LD_LIBRARY_PATH="${CACHE_DIR}/piper:${LD_LIBRARY_PATH:-}"

for name in stop_accepted slow_down_accepted proceed_accepted \
            stop_rejected slow_down_rejected proceed_rejected; do
    echo "Generating ${name}.wav: ${TEXT[$name]}"
    echo "${TEXT[$name]}" | "${CACHE_DIR}/piper/piper" \
        --model "${CACHE_DIR}/${VOICE}.onnx" \
        --output_file "${OUT_DIR}/${name}.wav" \
        --quiet
done

echo
echo "Done. Files in $(cd "${OUT_DIR}" && pwd):"
ls -l "${OUT_DIR}"/*.wav
