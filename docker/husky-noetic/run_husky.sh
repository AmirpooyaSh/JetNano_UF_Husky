#!/bin/bash

docker rm -f clearpath-husky-noetic 2>/dev/null || true

docker run \
  --name clearpath-husky-noetic \
  --rm \
  --privileged \
  --network host \
  --ipc host \
  -v /dev:/dev \
  -e HUSKY_PORT=/dev/ttyUSB0 \
  -e HUSKY_LOGITECH=1 \
  -e HUSKY_JOY_DEVICE=/dev/input/js0 \
  clearpath-husky-noetic:latest \
  roslaunch husky_base base.launch
