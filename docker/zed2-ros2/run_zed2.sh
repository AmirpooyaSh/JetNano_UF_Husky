#!/bin/bash

CONTAINER_NAME="zed2-ros2-humble"
IMAGE_NAME="zed2-ros2-humble:latest"

# Host ROS2 source directory
HOST_SRC="$HOME/catkin_ws/src"
CONTAINER_SRC="/root/ros2_ws/src"

# Persistent ZED model/resources directory
HOST_ZED_RESOURCES="$HOME/catkin_ws/docker/zed2-ros2/zed2-resources"
CONTAINER_ZED_RESOURCES="/usr/local/zed/resources"

mkdir -p "$HOST_ZED_RESOURCES"

# ---------------------------------------------------------------
# Create/reuse the persistent container.
# ---------------------------------------------------------------

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then

    # Persistent colcon build directories
    docker volume create zed2-build >/dev/null
    docker volume create zed2-install >/dev/null
    docker volume create zed2-log >/dev/null

    docker run -d \
      --name "$CONTAINER_NAME" \
      --runtime nvidia \
      --privileged \
      --network host \
      --ipc host \
      -e NVIDIA_DRIVER_CAPABILITIES=all \
      -v /dev:/dev \
      -v /dev/shm:/dev/shm \
      -v "$HOST_SRC:$CONTAINER_SRC" \
      -v "$HOST_ZED_RESOURCES:$CONTAINER_ZED_RESOURCES" \
      -v zed2-build:/root/ros2_ws/build \
      -v zed2-install:/root/ros2_ws/install \
      -v zed2-log:/root/ros2_ws/log \
      "$IMAGE_NAME" \
      sleep infinity

else
    docker start "$CONTAINER_NAME" >/dev/null
fi

# ---------------------------------------------------------------
# Refresh X11 authorization for the current SSH session.
# ---------------------------------------------------------------

XAUTH_FILE="/tmp/zed2-docker.xauth"

rm -f "$XAUTH_FILE"
touch "$XAUTH_FILE"

xauth nlist "$DISPLAY" \
  | sed -e 's/^..../ffff/' \
  | xauth -f "$XAUTH_FILE" nmerge -

chmod 644 "$XAUTH_FILE"

docker cp \
  "$XAUTH_FILE" \
  "$CONTAINER_NAME:/root/.Xauthority"

rm -f "$XAUTH_FILE"

# ---------------------------------------------------------------
# Install dependencies for packages in ros2_ws/src.
# ---------------------------------------------------------------

docker exec "$CONTAINER_NAME" bash -lc '
    apt-get update &&
    source /opt/ros/humble/setup.bash &&
    rosdep install \
      --from-paths /root/ros2_ws/src \
      --ignore-src \
      --rosdistro humble \
      -r -y
'

# ---------------------------------------------------------------
# Build workspace.
#
# build/install/log are persistent Docker volumes.
# Unchanged packages such as zed_components should therefore
# not be rebuilt from scratch every time.
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
# Launch Terminator INSIDE Docker.
# New tabs/splits remain inside the container.
# ---------------------------------------------------------------

docker exec \
  -e DISPLAY="$DISPLAY" \
  -e XAUTHORITY=/root/.Xauthority \
  -it "$CONTAINER_NAME" \
  terminator