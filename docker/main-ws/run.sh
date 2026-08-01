#!/usr/bin/env bash
set -e

# ===============================================================
# main-ws run script - v6
# catkin_make_isolated + persistent volumes + X11 GUI support
#
# Host ROS1 source:
#   ~/catkin_ws/src_ros1
#
# Docker ROS1 source:
#   /catkin_ws/src
#
# Skip automatic build:
#   RUN_ISOLATED_BUILD=0 ./run.sh
#
# Run rosdep before build:
#   RUN_ROSDEP=1 ./run.sh
# ===============================================================

IMAGE_NAME="main-ws:latest"
CONTAINER_NAME="main-ws"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST_SRC="$HOME/catkin_ws/src_ros1"
CONTAINER_WS="/catkin_ws"
CONTAINER_SRC="${CONTAINER_WS}/src"

RUN_ISOLATED_BUILD="${RUN_ISOLATED_BUILD:-1}"
RUN_ROSDEP="${RUN_ROSDEP:-0}"

BUILD_ISOLATED_VOLUME="main-ws-catkin-build-isolated"
DEVEL_ISOLATED_VOLUME="main-ws-catkin-devel-isolated"
INSTALL_ISOLATED_VOLUME="main-ws-catkin-install-isolated"
LOG_ISOLATED_VOLUME="main-ws-catkin-log-isolated"

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

if [ -z "${DISPLAY:-}" ]; then
    echo "[ERROR] DISPLAY is empty."
    echo "Reconnect to the Jetson using X11 forwarding:"
    echo "  ssh -Y start_jetson"
    exit 1
fi

if ! command -v xauth >/dev/null 2>&1; then
    echo "[ERROR] xauth is not installed on the Jetson host."
    echo "Install it with:"
    echo "  sudo apt update"
    echo "  sudo apt install -y xauth"
    exit 1
fi

# ---------------------------------------------------------------
# Persistent isolated catkin volumes
# ---------------------------------------------------------------

docker volume create "$BUILD_ISOLATED_VOLUME" >/dev/null
docker volume create "$DEVEL_ISOLATED_VOLUME" >/dev/null
docker volume create "$INSTALL_ISOLATED_VOLUME" >/dev/null
docker volume create "$LOG_ISOLATED_VOLUME" >/dev/null

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
        -v /dev:/dev \
        -v /dev/shm:/dev/shm \
        -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
        -v /dev/bus/usb:/dev/bus/usb \
        -v /run/udev:/run/udev:ro \
        -v "$HOST_SRC:$CONTAINER_SRC:rw" \
        -v "$BUILD_ISOLATED_VOLUME:$CONTAINER_WS/build_isolated" \
        -v "$DEVEL_ISOLATED_VOLUME:$CONTAINER_WS/devel_isolated" \
        -v "$INSTALL_ISOLATED_VOLUME:$CONTAINER_WS/install_isolated" \
        -v "$LOG_ISOLATED_VOLUME:$CONTAINER_WS/log" \
        -w "$CONTAINER_WS" \
        "$IMAGE_NAME" \
        sleep infinity

else

    echo "[INFO] Reusing container: $CONTAINER_NAME"
    docker start "$CONTAINER_NAME" >/dev/null

fi

# ---------------------------------------------------------------
# Refresh X11 authorization for current SSH session
# ---------------------------------------------------------------

XAUTH_FILE="/tmp/main-ws-docker.xauth"

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
# Optional rosdep
# ---------------------------------------------------------------

if [ "$RUN_ROSDEP" = "1" ]; then

    echo "[INFO] Installing ROS dependencies with rosdep..."

    docker exec "$CONTAINER_NAME" bash -lc '
        set -e

        source /opt/ros/noetic/setup.bash
        cd /catkin_ws

        rosdep update --rosdistro noetic || true

        rosdep install \
            --from-paths src \
            --ignore-src \
            --rosdistro noetic \
            -r \
            -y
    '

fi

# ---------------------------------------------------------------
# Optional isolated build
# ---------------------------------------------------------------

if [ "$RUN_ISOLATED_BUILD" = "1" ]; then

    echo "[INFO] Building workspace with catkin_make_isolated..."

    docker exec "$CONTAINER_NAME" bash -lc '
        set -e

        source /opt/ros/noetic/setup.bash
        cd /catkin_ws

        catkin_make_isolated \
            --install \
            --cmake-args \
                -DROS_VERSION=1 \
                -DLDMRS=0 \
                -DRASPBERRY=1 \
                -Wno-dev
    '

else

    echo "[INFO] Skipping automatic isolated build."

fi

# ---------------------------------------------------------------
# Shell used by every Terminator pane
# ---------------------------------------------------------------

docker exec "$CONTAINER_NAME" bash -lc "cat > /usr/local/bin/main-ws-shell <<'EOF'
#!/usr/bin/env bash

source /opt/ros/noetic/setup.bash

if [ -f /catkin_ws/install_isolated/setup.bash ]; then
    source /catkin_ws/install_isolated/setup.bash
elif [ -f /catkin_ws/devel_isolated/setup.bash ]; then
    source /catkin_ws/devel_isolated/setup.bash
fi

unset ROS_IP
unset ROS_HOSTNAME

export ROS_MASTER_URI=\"${ROS_MASTER_URI:-http://localhost:11311}\"
export DISPLAY=\"${DISPLAY}\"
export XAUTHORITY=/root/.Xauthority
export QT_X11_NO_MITSHM=1
export LIBGL_ALWAYS_SOFTWARE=1

cd /catkin_ws
exec bash
EOF

chmod +x /usr/local/bin/main-ws-shell"

# ---------------------------------------------------------------
# Host Terminator profile
# ---------------------------------------------------------------

TERMINATOR_CONFIG="/tmp/main-ws-terminator.conf"

cat > "$TERMINATOR_CONFIG" <<EOF
[global_config]
  suppress_multiple_term_dialog = True

[keybindings]

[profiles]
  [[default]]
    use_custom_command = True
    custom_command = docker exec -it ${CONTAINER_NAME} /usr/local/bin/main-ws-shell
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
echo "[INFO] DISPLAY=$DISPLAY"
echo "[INFO] Ctrl+Shift+O / Ctrl+Shift+E will stay inside: $CONTAINER_NAME"
echo "[INFO] Start RViz inside Docker with: rviz"

exec terminator \
    --config "$TERMINATOR_CONFIG" \
    --layout default