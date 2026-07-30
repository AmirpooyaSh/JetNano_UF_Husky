#!/bin/bash

docker rm -f zed2-ros2-humble 2>/dev/null || true

docker run \
  --name zed2-ros2-humble \
  --rm \
  -it \
  --runtime nvidia \
  --privileged \
  --network host \
  --ipc host \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -v /dev:/dev \
  -v /dev/shm:/dev/shm \
  zed2-ros2-humble:latest \
  bash
