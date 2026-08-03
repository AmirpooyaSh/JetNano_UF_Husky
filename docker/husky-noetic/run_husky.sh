#!/bin/bash
set -e

CONTAINER_NAME="clearpath-husky-noetic"
IMAGE_NAME="clearpath-husky-noetic:latest"

JETSON_IP="192.168.0.10"
ROS_MASTER_URI_VALUE="http://${JETSON_IP}:11311"

docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

docker run \
  --name "${CONTAINER_NAME}" \
  --rm \
  --privileged \
  --network host \
  --ipc host \
  -v /dev:/dev \
  -e ROS_MASTER_URI="${ROS_MASTER_URI_VALUE}" \
  -e ROS_IP="${JETSON_IP}" \
  -e HUSKY_PORT=/dev/ttyUSB0 \
  -e HUSKY_LOGITECH=1 \
  -e HUSKY_JOY_DEVICE=/dev/input/js0 \
  "${IMAGE_NAME}" \
  roslaunch husky_base base.launch