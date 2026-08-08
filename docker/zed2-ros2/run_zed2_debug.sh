#!/usr/bin/env bash
set -Eeuo pipefail

# =============================================================================
# zed2-ros2-humble runner
#
# - Always creates a fresh Docker container.
# - Persists build, install, and log in named Docker volumes.
# - Rebuilds only when source/image/build options changed.
# - Launches hand_gesture_recognition/zed2_skeleton.launch.py in background.
# - Creates /usr/local/bin/zed2-ros2-shell inside Docker.
# - Opens Terminator with every pane attached to the SAME Docker container.
# - Ctrl+Shift+O and Ctrl+Shift+E create new panes inside Docker.
# - X11 auth is created BEFORE the fresh container starts, exactly like the
#   working Husky runner pattern.
# - Run RViz2 from any Terminator pane with: rviz2
# =============================================================================

IMAGE_NAME="${IMAGE_NAME:-zed2-ros2-humble:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-zed2-ros2-humble}"

HOST_SRC="${HOST_SRC:-$HOME/catkin_ws/src}"
CONTAINER_WS="/root/ros2_ws"
CONTAINER_SRC="${CONTAINER_WS}/src"

HOST_ZED_RESOURCES="${HOST_ZED_RESOURCES:-$HOME/catkin_ws/docker/zed2-ros2/zed2-resources}"
CONTAINER_ZED_RESOURCES="/usr/local/zed/resources"

LAUNCH_PACKAGE="${LAUNCH_PACKAGE:-hand_gesture_recognition}"
LAUNCH_FILE="${LAUNCH_FILE:-zed2_skeleton.launch.py}"

FORCE_REBUILD="${FORCE_REBUILD:-0}"

BUILD_VOLUME="${BUILD_VOLUME:-zed2-build}"
INSTALL_VOLUME="${INSTALL_VOLUME:-zed2-install}"
LOG_VOLUME="${LOG_VOLUME:-zed2-log}"

BUILD_SIGNATURE_FILE="${CONTAINER_WS}/install/.zed2-build-signature"
LAUNCH_LOG="${CONTAINER_WS}/log/zed2-launch.log"

XAUTH_FILE="/tmp/${CONTAINER_NAME}-${UID}.xauth"
ENTER_CONTAINER_SCRIPT="/tmp/${CONTAINER_NAME}-${UID}-enter.sh"
TERMINATOR_CONFIG="/tmp/${CONTAINER_NAME}-${UID}-terminator.conf"

# IMPORTANT:
# Do not tie the Docker container lifetime to this host script's EXIT trap.
# Terminator can return control to the shell on some desktop setups. If an
# EXIT trap removes Docker at that moment, every Docker-backed Terminator pane
# dies immediately. The container is instead removed/recreated at the START
# of the next run.

error() {
    echo "[ERROR] $*" >&2
    exit 1
}

# -----------------------------------------------------------------------------
# Host checks
# -----------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || error "Docker is not installed or not in PATH."
command -v terminator >/dev/null 2>&1 || error "Terminator is not installed."
command -v xauth >/dev/null 2>&1 || error "xauth is not installed."
command -v sha256sum >/dev/null 2>&1 || error "sha256sum is required."

[ -n "${DISPLAY:-}" ] || error "DISPLAY is empty. Run this from the Jetson desktop session."
[ -d "$HOST_SRC" ] || error "ROS2 source directory does not exist: $HOST_SRC"
[ -d "$HOST_ZED_RESOURCES" ] || error "ZED resources directory does not exist: $HOST_ZED_RESOURCES"

docker info >/dev/null 2>&1 || error "Docker is not accessible."
docker image inspect "$IMAGE_NAME" >/dev/null 2>&1 || error "Docker image not found: $IMAGE_NAME"

# Remove stale helper/config files left by a previous invocation.
# The previous container is removed below before these paths are reused.
rm -f "$ENTER_CONTAINER_SCRIPT" "$TERMINATOR_CONFIG"

# -----------------------------------------------------------------------------
# X11 authorization
#
# IMPORTANT:
# This is intentionally the same pattern as the working Husky runner:
# create the xauth file first, THEN always create a fresh container that
# bind-mounts this exact file.
# -----------------------------------------------------------------------------
rm -f "$XAUTH_FILE"
touch "$XAUTH_FILE"

XAUTH_DATA="$(xauth nlist "$DISPLAY" 2>/dev/null || true)"
if [ -z "$XAUTH_DATA" ]; then
    XAUTH_DATA="$(xauth nlist 2>/dev/null | head -n 1 || true)"
fi

[ -n "$XAUTH_DATA" ] || error "No X11 authorization cookie was found for DISPLAY=$DISPLAY"

printf '%s\n' "$XAUTH_DATA" \
    | sed -e 's/^..../ffff/' \
    | xauth -f "$XAUTH_FILE" nmerge -

chmod 644 "$XAUTH_FILE"

# -----------------------------------------------------------------------------
# Persistent ROS2 build/install/log volumes
# -----------------------------------------------------------------------------
echo "[INFO] Ensuring persistent ROS2 volumes exist."
docker volume create "$BUILD_VOLUME" >/dev/null
docker volume create "$INSTALL_VOLUME" >/dev/null
docker volume create "$LOG_VOLUME" >/dev/null

# -----------------------------------------------------------------------------
# Always start a fresh container, exactly like the Husky runner.
#
# Named build/install/log volumes remain persistent.
# -----------------------------------------------------------------------------
echo "[INFO] Removing any previous container named: $CONTAINER_NAME"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

echo "[INFO] Starting fresh container: $CONTAINER_NAME"

docker run -d \
    --name "$CONTAINER_NAME" \
    --rm \
    --runtime nvidia \
    --privileged \
    --network host \
    --ipc host \
    -e NVIDIA_VISIBLE_DEVICES=all \
    -e NVIDIA_DRIVER_CAPABILITIES=all \
    -e DISPLAY="$DISPLAY" \
    -e XAUTHORITY=/tmp/.docker.xauth \
    -e QT_X11_NO_MITSHM=1 \
    -v /dev:/dev \
    -v /dev/shm:/dev/shm \
    -v /dev/bus/usb:/dev/bus/usb \
    -v /run/udev:/run/udev:ro \
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
    -v "$XAUTH_FILE:/tmp/.docker.xauth:ro" \
    -v "$HOST_SRC:$CONTAINER_SRC:rw" \
    -v "$HOST_ZED_RESOURCES:$CONTAINER_ZED_RESOURCES:rw" \
    -v "$BUILD_VOLUME:${CONTAINER_WS}/build" \
    -v "$INSTALL_VOLUME:${CONTAINER_WS}/install" \
    -v "$LOG_VOLUME:${CONTAINER_WS}/log" \
    -w "$CONTAINER_WS" \
    "$IMAGE_NAME" \
    sleep infinity >/dev/null


# -----------------------------------------------------------------------------
# Create the interactive ROS2 shell helper INSIDE the container.
# -----------------------------------------------------------------------------
docker exec -i "$CONTAINER_NAME" /bin/bash -c \
    'cat > /usr/local/bin/zed2-ros2-shell' <<'CONTAINER_SHELL_EOF'
#!/usr/bin/env bash
set -Ee -o pipefail

# Do not enable `set -u` while sourcing ROS2 environment scripts.
source /opt/ros/humble/setup.bash

if [ -f /root/ros2_ws/install/setup.bash ]; then
    source /root/ros2_ws/install/setup.bash
fi

export XAUTHORITY=/tmp/.docker.xauth
export QT_X11_NO_MITSHM=1

cd /root/ros2_ws

export PS1='[zed2-docker] \u@\h:\w\$ '

echo
echo "Container: ${HOSTNAME}"
echo "ROS2: Humble"
echo "Workspace: /root/ros2_ws"
echo "Launch: hand_gesture_recognition/zed2_skeleton.launch.py"
echo "Run RViz2 with: rviz2"
echo

exec /bin/bash --noprofile --norc -i
CONTAINER_SHELL_EOF

docker exec "$CONTAINER_NAME" chmod 755 /usr/local/bin/zed2-ros2-shell
docker exec "$CONTAINER_NAME" test -x /usr/local/bin/zed2-ros2-shell
docker exec "$CONTAINER_NAME" mkdir -p "$CONTAINER_WS/log"

# -----------------------------------------------------------------------------
# Detect source/image/build-option changes.
# This prevents apt-get/rosdep/colcon from running every startup.
# -----------------------------------------------------------------------------
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_NAME")"

SOURCE_METADATA_HASH="$({
    find "$HOST_SRC" \
        \( -type f -o -type l \) \
        -not -path '*/.git/*' \
        -not -path '*/build/*' \
        -not -path '*/install/*' \
        -not -path '*/log/*' \
        -printf '%P|%y|%s|%T@|%l\n' \
        | LC_ALL=C sort
} | sha256sum | awk '{print $1}')"

BUILD_CONFIGURATION='colcon build --symlink-install --parallel-workers 2 --cmake-args -DCMAKE_BUILD_TYPE=Release -DCUDA_TOOLKIT_ROOT_DIR=/usr/local/cuda -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs'

SOURCE_SIGNATURE="$(
    printf '%s\n%s\n%s\n' \
        "$IMAGE_ID" \
        "$SOURCE_METADATA_HASH" \
        "$BUILD_CONFIGURATION" \
        | sha256sum \
        | awk '{print $1}'
)"

STORED_SIGNATURE="$(
    docker exec "$CONTAINER_NAME" /bin/bash -lc \
        "cat '$BUILD_SIGNATURE_FILE' 2>/dev/null || true" \
        | tr -d '\r\n'
)"

NEEDS_BUILD=0
BUILD_REASON=""

if [ "$FORCE_REBUILD" = "1" ]; then
    NEEDS_BUILD=1
    BUILD_REASON="FORCE_REBUILD=1"
elif [ -z "$STORED_SIGNATURE" ]; then
    NEEDS_BUILD=1
    BUILD_REASON="first build for these persistent volumes"
elif [ "$SOURCE_SIGNATURE" != "$STORED_SIGNATURE" ]; then
    NEEDS_BUILD=1
    BUILD_REASON="source tree, image, or build configuration changed"
fi

if [ "$NEEDS_BUILD" -eq 1 ]; then
    echo "[INFO] Build required: $BUILD_REASON"

    docker exec "$CONTAINER_NAME" /bin/bash -lc '
        set -Ee -o pipefail
        apt-get update
        source /opt/ros/humble/setup.bash

        rosdep install \
            --from-paths /root/ros2_ws/src \
            --ignore-src \
            --rosdistro humble \
            -r \
            -y

        cd /root/ros2_ws

        colcon build \
            --symlink-install \
            --parallel-workers 2 \
            --cmake-args \
                -DCMAKE_BUILD_TYPE=Release \
                -DCUDA_TOOLKIT_ROOT_DIR=/usr/local/cuda \
                -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs
    '

    printf '%s\n' "$SOURCE_SIGNATURE" \
        | docker exec -i "$CONTAINER_NAME" /bin/bash -lc \
            "cat > '$BUILD_SIGNATURE_FILE'"

    echo "[INFO] ROS2 build completed."
else
    echo "[INFO] Persistent ROS2 build is current; skipping apt-get, rosdep, and colcon."
fi

# -----------------------------------------------------------------------------
# Launch the exact same launch file as your original run_zed2.sh.
# -----------------------------------------------------------------------------
echo "[INFO] Starting: ${LAUNCH_PACKAGE}/${LAUNCH_FILE}"

docker exec -d "$CONTAINER_NAME" /bin/bash -lc "
    set -Ee -o pipefail

    source /opt/ros/humble/setup.bash
    source /root/ros2_ws/install/setup.bash

    export XAUTHORITY=/tmp/.docker.xauth
    export QT_X11_NO_MITSHM=1

    cd /root/ros2_ws
    mkdir -p /root/ros2_ws/log

    exec ros2 launch '${LAUNCH_PACKAGE}' '${LAUNCH_FILE}' \
        >> '${LAUNCH_LOG}' 2>&1
"

sleep 4

if docker exec "$CONTAINER_NAME" /bin/bash -lc \
    "pgrep -af '[r]os2 launch ${LAUNCH_PACKAGE} ${LAUNCH_FILE}' >/dev/null"; then
    echo "[INFO] ${LAUNCH_PACKAGE}/${LAUNCH_FILE} is running."
else
    echo "[ERROR] ${LAUNCH_PACKAGE}/${LAUNCH_FILE} exited during startup." >&2
    echo "[ERROR] Recent launch output:" >&2
    docker exec "$CONTAINER_NAME" /bin/bash -lc \
        "tail -n 120 '${LAUNCH_LOG}' 2>/dev/null || true" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Host helper used by every Terminator pane.
# Exactly the same structure as the Husky runner.
# -----------------------------------------------------------------------------
cat > "$ENTER_CONTAINER_SCRIPT" <<ENTER_SCRIPT
#!/usr/bin/env bash
set -e

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "Container is not running: $CONTAINER_NAME" >&2
    exec /bin/bash
fi

exec docker exec -it \
    -w /root/ros2_ws \
    "$CONTAINER_NAME" \
    /usr/local/bin/zed2-ros2-shell
ENTER_SCRIPT

chmod 755 "$ENTER_CONTAINER_SCRIPT"

# -----------------------------------------------------------------------------
# Terminator profile.
# Every split inherits the Docker profile.
# -----------------------------------------------------------------------------
cat > "$TERMINATOR_CONFIG" <<TERMINATOR_EOF
[global_config]
  suppress_multiple_term_dialog = True
  always_split_with_profile = True

[keybindings]

[profiles]
  [[zed2-docker]]
    use_custom_command = True
    custom_command = /bin/bash $ENTER_CONTAINER_SCRIPT
    exit_action = close
    scrollback_infinite = True

[layouts]
  [[zed2-docker]]
    [[[window0]]]
      type = Window
      parent = ""
      profile = zed2-docker
    [[[terminal0]]]
      type = Terminal
      parent = window0
      profile = zed2-docker

[plugins]
TERMINATOR_EOF

echo "[INFO] Opening Terminator inside container: $CONTAINER_NAME"
echo "[INFO] Ctrl+Shift+O: horizontal split inside Docker"
echo "[INFO] Ctrl+Shift+E: vertical split inside Docker"
echo "[INFO] Run RViz2 from any pane with: rviz2"
echo "[INFO] ROS2 launch log: $LAUNCH_LOG"
echo "[INFO] Closing Terminator does NOT remove the container; the next run recreates it."

# Start Terminator, but DO NOT remove the Docker container when this launcher
# script returns. Every pane connects to the already-running container through
# ENTER_CONTAINER_SCRIPT.
terminator \
    --no-dbus \
    --config "$TERMINATOR_CONFIG" \
    --layout zed2-docker &

TERMINATOR_PID=$!
disown "$TERMINATOR_PID" 2>/dev/null || true

echo "[INFO] Terminator started (PID: $TERMINATOR_PID)."
echo "[INFO] Docker remains running: $CONTAINER_NAME"
echo "[INFO] Run RViz2 from a Docker Terminator pane with: rviz2"
