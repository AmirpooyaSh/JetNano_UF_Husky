#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Model to load. Examples: tiny.en, base.en, small.en, medium.en,
# distil-small.en, large-v3-turbo
# ---------------------------------------------------------------------------
MODEL_NAME="base.en"

CONTAINER_NAME="whisper-live"
IMAGE_NAME="whisper-live-jetson:latest"
PORT=9090
MODEL_CACHE="/home/administrator/catkin_ws/docker/whisper-live/models"
STARTUP_TIMEOUT=900

mkdir -p "${MODEL_CACHE}"

if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
    echo "ERROR: Image ${IMAGE_NAME} does not exist. Run build.sh first."
    exit 1
fi

IMAGE_ID="$(docker image inspect -f '{{.Id}}' "${IMAGE_NAME}")"

# Recreate the container when the model or the image changed.
if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
    CURRENT_MODEL="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${CONTAINER_NAME}" \
        | sed -n 's/^WHISPER_MODEL=//p')"
    CURRENT_IMAGE="$(docker inspect -f '{{.Image}}' "${CONTAINER_NAME}")"

    if [ "${CURRENT_MODEL}" != "${MODEL_NAME}" ] || [ "${CURRENT_IMAGE}" != "${IMAGE_ID}" ]; then
        echo "Recreating ${CONTAINER_NAME} (model: '${CURRENT_MODEL}' -> '${MODEL_NAME}')..."
        docker rm -f "${CONTAINER_NAME}" >/dev/null
    fi
fi

if ! docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
    echo "Creating ${CONTAINER_NAME} with ${MODEL_NAME}..."
    docker create \
        --name "${CONTAINER_NAME}" \
        --runtime nvidia \
        --network host \
        --ipc host \
        -e WHISPER_MODEL="${MODEL_NAME}" \
        -e WHISPER_PORT="${PORT}" \
        -v "${MODEL_CACHE}:/models" \
        "${IMAGE_NAME}" >/dev/null
fi

if [ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" != "true" ]; then
    echo "Starting ${CONTAINER_NAME}..."
    docker start "${CONTAINER_NAME}" >/dev/null
fi

# The server opens its port only after the model is loaded and warmed up.
echo "Waiting for WhisperLive to load ${MODEL_NAME}..."

SECONDS=0
until (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; do
    if [ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" != "true" ]; then
        echo "ERROR: ${CONTAINER_NAME} stopped. Last logs:"
        docker logs --tail 40 "${CONTAINER_NAME}"
        exit 1
    fi

    if [ "${SECONDS}" -ge "${STARTUP_TIMEOUT}" ]; then
        echo "ERROR: WhisperLive not ready after ${STARTUP_TIMEOUT} s."
        exit 1
    fi

    sleep 1
done

docker logs "${CONTAINER_NAME}" 2>&1 | grep "WHISPERLIVE_READY" | tail -1
echo "${MODEL_NAME} is loaded and ready on port ${PORT}."
