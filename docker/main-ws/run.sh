#!/usr/bin/env bash
set -e

# ===============================================================
# main-ws run script - v4
#
# Host:
#   ~/catkin_ws/src_ros1
#
# Docker:
#   /catkin_ws/src
#
# Terminator runs on the HOST, but its DEFAULT profile launches
# every terminal pane directly inside the main-ws container.
# ===============================================================

IMAGE_NAME="main-ws:latest"
CONTAINER_NAME="main-ws"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST_SRC="$HOME/catkin_ws/src_ros1"
CONTAINER_WS="/catkin_ws"
CONTAINER_SRC="${CONTAINER_WS}/src"

BUILD_VOLUME="main-ws-catkin-build"
DEVEL_VOLUME="main-ws-catkin-devel"
LOGS_VOLUME="main-ws-catkin-logs"

# ---------------------------------------------------------------
# Checks
# ---------------------------------------------------------------

if [ ! -d "$HOST_SRC" ]; then
    echo "[ERROR] ROS1 source directory does not exist:"
    echo "        $HOST_SRC"
    exit 1
fi

if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "[ERROR] Docker image not found: $IMAGE_NAME"
    echo "Build it first:"
    echo "  cd $SCRIPT_DIR"
    echo "  ./build.sh"
    exit 1
fi

if ! command -v terminator >/dev/null 2>&1; then
    echo "[ERROR] Terminator is not installed on the Jetson host."
    echo "Install it with:"
    echo "  sudo apt update"
    echo "  sudo apt install -y terminator"
    exit 1
fi

# ---------------------------------------------------------------
# Persistent ROS1 catkin volumes
# ---------------------------------------------------------------

docker volume create "$BUILD_VOLUME" >/dev/null
docker volume create "$DEVEL_VOLUME" >/dev/null
docker volume create "$LOGS_VOLUME" >/dev/null

# ---------------------------------------------------------------
# Create/reuse main-ws container
# ---------------------------------------------------------------

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then

    echo "[INFO] Creating container: $CONTAINER_NAME"

    docker run -d \
        --name "$CONTAINER_NAME" \
        --runtime nvidia \
        --privileged \
        --network host \
        --ipc host \
        -e NVIDIA_VISIBLE_DEVICES=all \
        -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
        -e ROS_MASTER_URI="${ROS_MASTER_URI:-http://localhost:11311}" \
        -e ROS_IP="${ROS_IP:-}" \
        -e ROS_HOSTNAME="${ROS_HOSTNAME:-}" \
        -v /dev:/dev \
        -v /dev/shm:/dev/shm \
        -v /dev/bus/usb:/dev/bus/usb \
        -v /run/udev:/run/udev:ro \
        -v "$HOST_SRC:$CONTAINER_SRC:rw" \
        -v "$BUILD_VOLUME:$CONTAINER_WS/build" \
        -v "$DEVEL_VOLUME:$CONTAINER_WS/devel" \
        -v "$LOGS_VOLUME:$CONTAINER_WS/logs" \
        -w "$CONTAINER_WS" \
        "$IMAGE_NAME" \
        sleep infinity

else

    echo "[INFO] Reusing container: $CONTAINER_NAME"
    docker start "$CONTAINER_NAME" >/dev/null

fi

# ---------------------------------------------------------------
# Temporary Terminator config
#
# IMPORTANT:
# The Docker command is assigned to the DEFAULT Terminator profile.
# New panes created with Ctrl+Shift+O / Ctrl+Shift+E use this same
# default profile and therefore enter main-ws automatically.
# ---------------------------------------------------------------

TERMINATOR_CONFIG="/tmp/main-ws-terminator.conf"

cat > "$TERMINATOR_CONFIG" <<EOF
[global_config]
  suppress_multiple_term_dialog = True

[keybindings]

[profiles]
  [[default]]
    use_custom_command = True
    custom_command = docker exec -it ${CONTAINER_NAME} bash
    exit_action = restart

[layouts]
  [[default]]
    [[[window0]]]
      type = Window
      parent = ""
    [[[terminal0]]]
      type = Terminal
      parent = window0
      profile = default

[plugins]
EOF

echo "[INFO] Opening Terminator."
echo "[INFO] Ctrl+Shift+O / Ctrl+Shift+E will open panes inside: $CONTAINER_NAME"

exec terminator --config "$TERMINATOR_CONFIG" --layout default