#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE_NAME="${IMAGE_NAME:-whisper-live-jetson:latest}"
BASE_IMAGE="${BASE_IMAGE:-whisper-jetson:base-en}"
BUILD_JOBS="${BUILD_JOBS:-4}"

if ! docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
    echo "ERROR: Base image ${BASE_IMAGE} does not exist."
    exit 1
fi

echo "Building ${IMAGE_NAME} from ${BASE_IMAGE} (jobs=${BUILD_JOBS})..."

# --network host avoids the bridge/veth failure seen on this Jetson.
docker build \
    --network host \
    --build-arg BASE_IMAGE="${BASE_IMAGE}" \
    --build-arg BUILD_JOBS="${BUILD_JOBS}" \
    -t "${IMAGE_NAME}" \
    "${SCRIPT_DIR}"

echo "Built ${IMAGE_NAME}."
