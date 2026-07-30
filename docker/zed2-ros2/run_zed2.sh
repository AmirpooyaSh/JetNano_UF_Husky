#!/bin/bash

CONTAINER_NAME="zed2-ros2-humble"
IMAGE_NAME="zed2-ros2-humble:latest"

HOST_WRAPPER="$HOME/catkin_ws/src/zed-ros2-wrapper"
CONTAINER_WRAPPER="/root/ros2_ws/src/zed-ros2-wrapper"

# ---------------------------------------------------------------
# Create/reuse the persistent container.
# It stays alive with sleep infinity.
# ---------------------------------------------------------------

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    docker volume create zed2-resources >/dev/null

    docker run -d \
      --name "$CONTAINER_NAME" \
      --runtime nvidia \
      --privileged \
      --network host \
      --ipc host \
      -e NVIDIA_DRIVER_CAPABILITIES=all \
      -v /dev:/dev \
      -v /dev/shm:/dev/shm \
      -v "$HOST_WRAPPER:$CONTAINER_WRAPPER" \
      -v zed2-resources:/usr/local/zed/resources \
      "$IMAGE_NAME" \
      sleep infinity
else
    docker start "$CONTAINER_NAME" >/dev/null
fi

# ---------------------------------------------------------------
# Refresh the X11 cookie from the CURRENT SSH session.
# ---------------------------------------------------------------

XAUTH_FILE="/tmp/zed2-docker.xauth"

rm -f "$XAUTH_FILE"
touch "$XAUTH_FILE"

xauth nlist "$DISPLAY" \
  | sed -e 's/^..../ffff/' \
  | xauth -f "$XAUTH_FILE" nmerge -

chmod 644 "$XAUTH_FILE"
docker cp "$XAUTH_FILE" "$CONTAINER_NAME:/root/.Xauthority"
rm -f "$XAUTH_FILE"

# ---------------------------------------------------------------
# Install dependencies for the host-mounted wrapper.
# ---------------------------------------------------------------

docker exec "$CONTAINER_NAME" bash -lc '
    apt-get update &&
    source /opt/ros/humble/setup.bash &&
    rosdep install \
      --from-paths /root/ros2_ws/src/zed-ros2-wrapper \
      --ignore-src \
      --rosdistro humble \
      -r -y
'

# ---------------------------------------------------------------
# Build the host-mounted wrapper.
# ---------------------------------------------------------------

docker exec "$CONTAINER_NAME" bash -lc '
    cd /root/ros2_ws &&
    source /opt/ros/humble/setup.bash &&
    colcon build \
      --symlink-install \
      --parallel-workers 2 \
      --cmake-args \
        -DCMAKE_BUILD_TYPE=Release \
        -DCUDA_TOOLKIT_ROOT_DIR=/usr/local/cuda \
        -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs
'

# ---------------------------------------------------------------
# IMPORTANT:
# Terminator itself is launched INSIDE the Docker container.
# Therefore every new tab/split also belongs to the container.
# ---------------------------------------------------------------

docker exec \
  -e DISPLAY="$DISPLAY" \
  -e XAUTHORITY=/root/.Xauthority \
  -it "$CONTAINER_NAME" \
  terminator