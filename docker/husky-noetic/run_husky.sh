#!/usr/bin/env bash
set -Eeuo pipefail

# =============================================================================
# clearpath-husky-noetic runner
#
# - Always creates a fresh Docker container.
# - Mounts ~/catkin_ws/src_ros1 at /catkin_ws/src.
# - Persists build_isolated, devel_isolated, install_isolated, and log.
# - Runs rosdep before every required isolated build.
# - Builds with the custom SICK-compatible catkin_make_isolated options.
# - Launches ONLY robot_bringup/robot.launch.
# - Creates /usr/local/bin/clearpath-husky-shell inside Docker and chmods it executable.
# - Opens Terminator with every pane attached to the same Docker container.
# - Ctrl+Shift+O and Ctrl+Shift+E create new panes inside Docker.
# - Supports USB devices, NVIDIA runtime, X11, RViz, and other GUI tools.
# =============================================================================

IMAGE_NAME="${IMAGE_NAME:-clearpath-husky-noetic:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-clearpath-husky-noetic}"

JETSON_IP="${JETSON_IP:-192.168.0.10}"
ROS_MASTER_URI_VALUE="http://${JETSON_IP}:11311"

HOST_SRC="${HOST_SRC:-$HOME/catkin_ws/src_ros1}"
CONTAINER_WS="/catkin_ws"
CONTAINER_SRC="${CONTAINER_WS}/src"

ROBOT_LAUNCH_PACKAGE="${ROBOT_LAUNCH_PACKAGE:-robot_bringup}"
ROBOT_LAUNCH_FILE="${ROBOT_LAUNCH_FILE:-robot.launch}"
HOST_ROBOT_LAUNCH="${HOST_SRC}/${ROBOT_LAUNCH_PACKAGE}/launch/${ROBOT_LAUNCH_FILE}"

HUSKY_PORT="${HUSKY_PORT:-/dev/ttyUSB0}"
HUSKY_LOGITECH="${HUSKY_LOGITECH:-1}"
HUSKY_JOY_DEVICE="${HUSKY_JOY_DEVICE:-/dev/input/js0}"

HUSKY_URDF_EXTRAS="${HUSKY_URDF_EXTRAS:-/catkin_ws/src/robot_bringup/urdf/husky_camera_frame.urdf.xacro}"

# Husky built-in SICK LMS1xx URDF configuration
HUSKY_LMS1XX_ENABLED="${HUSKY_LMS1XX_ENABLED:-1}"
HUSKY_LMS1XX_PREFIX="${HUSKY_LMS1XX_PREFIX:-front}"
HUSKY_LMS1XX_PARENT="${HUSKY_LMS1XX_PARENT:-top_plate_link}"

# Position of the LiDAR mount relative to top_plate_link
# Format: x y z, in metres
HUSKY_LMS1XX_XYZ="${HUSKY_LMS1XX_XYZ:-0.2206 0.0 0.00635}"

# Orientation relative to top_plate_link
# Format: roll pitch yaw, in radians
HUSKY_LMS1XX_RPY="${HUSKY_LMS1XX_RPY:-0.0 0.0 0.0}"

# 1 = include Clearpath's physical LMS1xx mounting tower
# 0 = sensor without the tower mesh
HUSKY_LMS1XX_TOWER="${HUSKY_LMS1XX_TOWER:-1}"

HUSKY_LMS1XX_TOPIC="${HUSKY_LMS1XX_TOPIC:-scan}"


FORCE_REBUILD="${FORCE_REBUILD:-0}"
RVIZ_SOFTWARE_RENDERING="${RVIZ_SOFTWARE_RENDERING:-1}"

BUILD_ISOLATED_VOLUME="${BUILD_ISOLATED_VOLUME:-clearpath-husky-noetic-build-isolated}"
DEVEL_ISOLATED_VOLUME="${DEVEL_ISOLATED_VOLUME:-clearpath-husky-noetic-devel-isolated}"
INSTALL_ISOLATED_VOLUME="${INSTALL_ISOLATED_VOLUME:-clearpath-husky-noetic-install-isolated}"
LOG_ISOLATED_VOLUME="${LOG_ISOLATED_VOLUME:-clearpath-husky-noetic-log-isolated}"

BUILD_SIGNATURE_FILE="${CONTAINER_WS}/install_isolated/.src_ros1-build-signature"
LAUNCH_LOG="${CONTAINER_WS}/log/robot_bringup/robot-launch.log"

XAUTH_FILE="/tmp/${CONTAINER_NAME}-${UID}.xauth"
ENTER_CONTAINER_SCRIPT="/tmp/${CONTAINER_NAME}-${UID}-enter.sh"
TERMINATOR_CONFIG="/tmp/${CONTAINER_NAME}-${UID}-terminator.conf"

CONTAINER_STARTED=0

cleanup() {
    local exit_code=$?

    if [ "$CONTAINER_STARTED" -eq 1 ]; then
        echo "[INFO] Removing container: ${CONTAINER_NAME}"
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi

    rm -f \
        "$XAUTH_FILE" \
        "$ENTER_CONTAINER_SCRIPT" \
        "$TERMINATOR_CONFIG"

    exit "$exit_code"
}
trap cleanup EXIT INT TERM

error() {
    echo "[ERROR] $*" >&2
    exit 1
}

# -----------------------------------------------------------------------------
# Host checks
# -----------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || error "Docker is not installed or not in PATH."
command -v terminator >/dev/null 2>&1 || error "Terminator is not installed. Run: sudo apt update && sudo apt install -y terminator"
command -v xauth >/dev/null 2>&1 || error "xauth is not installed. Run: sudo apt update && sudo apt install -y xauth"
command -v sha256sum >/dev/null 2>&1 || error "sha256sum is required."

[ -n "${DISPLAY:-}" ] || error "DISPLAY is empty. Use the Jetson desktop or an X11-forwarded SSH session."
[ -d "$HOST_SRC" ] || error "ROS1 source directory does not exist: $HOST_SRC"
[ -f "$HOST_ROBOT_LAUNCH" ] || error "Merged launch file does not exist: $HOST_ROBOT_LAUNCH"

docker info >/dev/null 2>&1 || error "Docker is not accessible. Check the Docker service and your user permissions."
docker image inspect "$IMAGE_NAME" >/dev/null 2>&1 || error "Docker image not found: $IMAGE_NAME"

case "$HUSKY_LOGITECH" in
    0|1) ;;
    *) error "HUSKY_LOGITECH must be 0 or 1; received: $HUSKY_LOGITECH" ;;
esac

if [ ! -e "$HUSKY_PORT" ]; then
    echo "[WARN] Husky serial device is not currently present: $HUSKY_PORT"
fi

if [ "$HUSKY_LOGITECH" = "1" ] && [ ! -e "$HUSKY_JOY_DEVICE" ]; then
    echo "[WARN] Logitech joystick device is not currently present: $HUSKY_JOY_DEVICE"
fi

# -----------------------------------------------------------------------------
# X11 authorization for RViz and other GUI applications
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
# Persistent isolated catkin volumes
# -----------------------------------------------------------------------------
echo "[INFO] Ensuring persistent catkin volumes exist."
docker volume create "$BUILD_ISOLATED_VOLUME" >/dev/null
docker volume create "$DEVEL_ISOLATED_VOLUME" >/dev/null
docker volume create "$INSTALL_ISOLATED_VOLUME" >/dev/null
docker volume create "$LOG_ISOLATED_VOLUME" >/dev/null

# -----------------------------------------------------------------------------
# Always start a fresh container; never reuse an old one.
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
    -e RVIZ_SOFTWARE_RENDERING="$RVIZ_SOFTWARE_RENDERING" \
    -e ROS_MASTER_URI="$ROS_MASTER_URI_VALUE" \
    -e ROS_IP="$JETSON_IP" \
    -e ROS_LOG_DIR="${CONTAINER_WS}/log/ros" \
    -e HUSKY_PORT="$HUSKY_PORT" \
    -e HUSKY_LOGITECH="$HUSKY_LOGITECH" \
    -e HUSKY_JOY_DEVICE="$HUSKY_JOY_DEVICE" \
    -e HUSKY_LMS1XX_ENABLED="$HUSKY_LMS1XX_ENABLED" \
    -e HUSKY_LMS1XX_PREFIX="$HUSKY_LMS1XX_PREFIX" \
    -e HUSKY_LMS1XX_PARENT="$HUSKY_LMS1XX_PARENT" \
    -e HUSKY_LMS1XX_XYZ="$HUSKY_LMS1XX_XYZ" \
    -e HUSKY_LMS1XX_RPY="$HUSKY_LMS1XX_RPY" \
    -e HUSKY_LMS1XX_TOWER="$HUSKY_LMS1XX_TOWER" \
    -e HUSKY_LMS1XX_TOPIC="$HUSKY_LMS1XX_TOPIC" \
    -e HUSKY_URDF_EXTRAS="$HUSKY_URDF_EXTRAS" \
    -v /dev:/dev \
    -v /dev/shm:/dev/shm \
    -v /dev/bus/usb:/dev/bus/usb \
    -v /run/udev:/run/udev:ro \
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
    -v "$XAUTH_FILE:/tmp/.docker.xauth:ro" \
    -v "$HOST_SRC:$CONTAINER_SRC:rw" \
    -v "$BUILD_ISOLATED_VOLUME:${CONTAINER_WS}/build_isolated" \
    -v "$DEVEL_ISOLATED_VOLUME:${CONTAINER_WS}/devel_isolated" \
    -v "$INSTALL_ISOLATED_VOLUME:${CONTAINER_WS}/install_isolated" \
    -v "$LOG_ISOLATED_VOLUME:${CONTAINER_WS}/log" \
    -w "$CONTAINER_WS" \
    "$IMAGE_NAME" \
    sleep infinity >/dev/null

CONTAINER_STARTED=1

# -----------------------------------------------------------------------------
# Create the interactive shell helper INSIDE the running container.
# chmod must happen after the file exists in the container.
# -----------------------------------------------------------------------------
docker exec -i "$CONTAINER_NAME" /bin/bash -c \
    'cat > /usr/local/bin/clearpath-husky-shell' <<'CONTAINER_SHELL_EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

source /opt/ros/noetic/setup.bash

if [ -f /catkin_ws/install_isolated/setup.bash ]; then
    source /catkin_ws/install_isolated/setup.bash
elif [ -f /catkin_ws/devel_isolated/setup.bash ]; then
    source /catkin_ws/devel_isolated/setup.bash
fi

export XAUTHORITY=/tmp/.docker.xauth
export QT_X11_NO_MITSHM=1
export ROS_LOG_DIR=/catkin_ws/log/ros

if [ "${RVIZ_SOFTWARE_RENDERING:-1}" = "1" ]; then
    export LIBGL_ALWAYS_SOFTWARE=1
else
    unset LIBGL_ALWAYS_SOFTWARE
fi

mkdir -p /catkin_ws/log/ros
cd /catkin_ws

export PS1='[husky-docker] \u@\h:\w\$ '

echo
echo "Container: ${HOSTNAME}"
echo "ROS_MASTER_URI=${ROS_MASTER_URI}"
echo "ROS_IP=${ROS_IP}"
echo "HUSKY_PORT=${HUSKY_PORT:-unset}"
echo "Launch log: /catkin_ws/log/robot_bringup/robot-launch.log"
echo

exec /bin/bash --noprofile --norc -i
CONTAINER_SHELL_EOF

# The requested permission fix happens here, inside the container.
docker exec "$CONTAINER_NAME" \
    chmod 755 /usr/local/bin/clearpath-husky-shell

docker exec "$CONTAINER_NAME" \
    test -x /usr/local/bin/clearpath-husky-shell

docker exec "$CONTAINER_NAME" /bin/bash -lc \
    'mkdir -p /catkin_ws/log/ros /catkin_ws/log/robot_bringup'

# -----------------------------------------------------------------------------
# Detect source/image/build-option changes.
# -----------------------------------------------------------------------------
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_NAME")"

SOURCE_METADATA_HASH="$({
    find "$HOST_SRC" \
        \( -type f -o -type l \) \
        -not -path '*/.git/*' \
        -not -path '*/build/*' \
        -not -path '*/build_isolated/*' \
        -not -path '*/devel/*' \
        -not -path '*/devel_isolated/*' \
        -not -path '*/install/*' \
        -not -path '*/install_isolated/*' \
        -printf '%P|%y|%s|%T@|%l\n' \
        | LC_ALL=C sort
} | sha256sum | awk '{print $1}')"

BUILD_CONFIGURATION='catkin_make_isolated --install --cmake-args -DROS_VERSION=1 -DLDMRS=0 -DRASPBERRY=1 -Wno-dev'

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
    echo "[INFO] Updating rosdep and installing workspace dependencies."

    docker exec "$CONTAINER_NAME" /bin/bash -lc '
        set -Ee -o pipefail
        source /opt/ros/noetic/setup.bash
        cd /catkin_ws

        rosdep update --rosdistro noetic
        rosdep install \
            --from-paths src \
            --ignore-src \
            --rosdistro noetic \
            -r \
            -y
    '

    echo "[INFO] Running the required SICK-compatible isolated build."

    docker exec "$CONTAINER_NAME" /bin/bash -lc '
        set -Ee -o pipefail
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

    printf '%s\n' "$SOURCE_SIGNATURE" \
        | docker exec -i "$CONTAINER_NAME" /bin/bash -lc \
            "cat > '$BUILD_SIGNATURE_FILE'"

    echo "[INFO] Isolated build completed."
else
    echo "[INFO] Persistent isolated build is current; skipping rebuild."
fi

# -----------------------------------------------------------------------------
# Launch ONLY robot_bringup/robot.launch.
# robot.launch contains both the Husky base and SICK LiDAR includes.
# -----------------------------------------------------------------------------
echo "[INFO] Starting only: ${ROBOT_LAUNCH_PACKAGE}/${ROBOT_LAUNCH_FILE}"
echo "[INFO] HUSKY_PORT=$HUSKY_PORT"
echo "[INFO] HUSKY_LOGITECH=$HUSKY_LOGITECH"
echo "[INFO] HUSKY_JOY_DEVICE=$HUSKY_JOY_DEVICE"

docker exec -d "$CONTAINER_NAME" /bin/bash -lc "
    set -Ee -o pipefail
    source /opt/ros/noetic/setup.bash

    if [ -f /catkin_ws/install_isolated/setup.bash ]; then
        source /catkin_ws/install_isolated/setup.bash
    elif [ -f /catkin_ws/devel_isolated/setup.bash ]; then
        source /catkin_ws/devel_isolated/setup.bash
    fi

    export ROS_LOG_DIR=/catkin_ws/log/ros
    mkdir -p \"\$ROS_LOG_DIR\" /catkin_ws/log/robot_bringup
    cd /catkin_ws

    exec roslaunch '${ROBOT_LAUNCH_PACKAGE}' '${ROBOT_LAUNCH_FILE}' \
        >> '${LAUNCH_LOG}' 2>&1
"

sleep 4

if docker exec "$CONTAINER_NAME" /bin/bash -lc \
    "pgrep -af '[r]oslaunch ${ROBOT_LAUNCH_PACKAGE} ${ROBOT_LAUNCH_FILE}' >/dev/null"; then
    echo "[INFO] ${ROBOT_LAUNCH_PACKAGE}/${ROBOT_LAUNCH_FILE} is running."
else
    echo "[ERROR] ${ROBOT_LAUNCH_PACKAGE}/${ROBOT_LAUNCH_FILE} exited during startup." >&2
    echo "[ERROR] Recent launch output:" >&2
    docker exec "$CONTAINER_NAME" /bin/bash -lc \
        "tail -n 120 '${LAUNCH_LOG}' 2>/dev/null || true" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Host command used by every Terminator pane.
# Every pane runs the executable helper created inside this container.
# -----------------------------------------------------------------------------
cat > "$ENTER_CONTAINER_SCRIPT" <<ENTER_SCRIPT
#!/usr/bin/env bash
set -e

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "Container is not running: $CONTAINER_NAME" >&2
    exec /bin/bash
fi

exec docker exec -it \
    -w /catkin_ws \
    "$CONTAINER_NAME" \
    /usr/local/bin/clearpath-husky-shell
ENTER_SCRIPT

chmod 755 "$ENTER_CONTAINER_SCRIPT"

# -----------------------------------------------------------------------------
# Terminator profile.
# Every new split inherits this profile and runs ENTER_CONTAINER_SCRIPT,
# therefore Ctrl+Shift+O and Ctrl+Shift+E remain inside the same container.
# -----------------------------------------------------------------------------
cat > "$TERMINATOR_CONFIG" <<TERMINATOR_EOF
[global_config]
  suppress_multiple_term_dialog = True
  always_split_with_profile = True

[keybindings]

[profiles]
  [[husky-docker]]
    use_custom_command = True
    custom_command = /bin/bash $ENTER_CONTAINER_SCRIPT
    exit_action = close
    scrollback_infinite = True

[layouts]
  [[husky-docker]]
    [[[window0]]]
      type = Window
      parent = ""
      profile = husky-docker
    [[[terminal0]]]
      type = Terminal
      parent = window0
      profile = husky-docker

[plugins]
TERMINATOR_EOF

echo "[INFO] Opening Terminator inside container: $CONTAINER_NAME"
echo "[INFO] Ctrl+Shift+O: horizontal split inside Docker"
echo "[INFO] Ctrl+Shift+E: vertical split inside Docker"
echo "[INFO] Run RViz from any pane with: rviz"
echo "[INFO] Robot launch log: $LAUNCH_LOG"
echo "[INFO] Closing the Terminator window removes the container; named volumes remain."

# --no-dbus prevents an already-running Terminator process from ignoring this
# temporary Docker-specific profile.
terminator \
    --no-dbus \
    --config "$TERMINATOR_CONFIG" \
    --layout husky-docker