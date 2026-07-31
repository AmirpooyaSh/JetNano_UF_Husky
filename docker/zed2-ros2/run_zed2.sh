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
# Create/reuse persistent container
# ---------------------------------------------------------------

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then

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

    # Restart clears any previously running ROS2 nodes
    # without deleting container modifications.
    docker restart "$CONTAINER_NAME" >/dev/null

fi

# ---------------------------------------------------------------
# Install ROS dependencies
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
# Build workspace
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
# Automatically launch:
#
#   1. ZED2
#   2. Skeleton visualizer / gesture recognition
#   3. ROS2 -> ROS1 ZeroMQ sender
#
# Runs detached in the background.
# ---------------------------------------------------------------

docker exec -d "$CONTAINER_NAME" bash -lc '
    cd /root/ros2_ws &&
    source /opt/ros/humble/setup.bash &&
    source install/setup.bash &&
    exec ros2 launch \
      hand_gesture_recognition \
      zed2_skeleton.launch.py
'

echo "ZED2 ROS2 stack started."
echo "Container: $CONTAINER_NAME"