#!/usr/bin/env bash

set -euo pipefail

CONTAINER_NAME="ollama"
MODEL_NAME="llama3.2:1b"
OLLAMA_URL="http://127.0.0.1:11434"

if ! docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
    echo "ERROR: Ollama container does not exist."
    exit 1
fi

if [ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" != "true" ]; then
    echo "Starting Ollama container..."
    docker start "${CONTAINER_NAME}"
fi

echo "Waiting for Ollama server..."

until curl --silent --fail "${OLLAMA_URL}/api/tags" >/dev/null; do
    sleep 1
done

if ! docker exec "${CONTAINER_NAME}" ollama list \
    | awk 'NR > 1 {print $1}' \
    | grep --fixed-strings --quiet "${MODEL_NAME}"; then

    echo "Downloading ${MODEL_NAME}..."
    docker exec "${CONTAINER_NAME}" ollama pull "${MODEL_NAME}"
fi

echo "Loading ${MODEL_NAME} into memory..."

curl --silent --fail \
    "${OLLAMA_URL}/api/generate" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"${MODEL_NAME}\",
        \"prompt\": \"Reply only with READY\",
        \"stream\": false,
        \"keep_alive\": -1
    }"

echo
echo "${MODEL_NAME} is loaded and ready."