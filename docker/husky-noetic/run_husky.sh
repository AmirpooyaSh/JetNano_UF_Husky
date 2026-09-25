#!/usr/bin/env bash
set -Eeuo pipefail

# =============================================================================
# clearpath-husky-noetic HEADLESS runner
#
# - Always creates a fresh Docker container.
# - Mounts ~/catkin_ws/src_ros1 at /catkin_ws/src.
# - Persists build_isolated, devel_isolated, install_isolated, and log.
# - Rebuilds only when source/image/build configuration changed.
# - Launches ONLY robot_bringup/robot.launch.
# - robot.launch now also launches the ROS2 -> ROS1 gesture receiver.
# - Does NOT open Terminator or any GUI.
# - roslaunch stays attached to this script; Ctrl+C stops it and removes the
#   temporary container while preserving the named build volumes.
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
HUSKY_JOY_DEVICE="${HUSKY_JOY_DEVICE:-/dev/input/js1}"

HUSKY_URDF_EXTRAS="${HUSKY_URDF_EXTRAS:-/catkin_ws/src/robot_bringup/urdf/husky_camera_frame.urdf.xacro}"

HUSKY_LMS1XX_ENABLED="${HUSKY_LMS1XX_ENABLED:-1}"
HUSKY_LMS1XX_PREFIX="${HUSKY_LMS1XX_PREFIX:-front}"
HUSKY_LMS1XX_PARENT="${HUSKY_LMS1XX_PARENT:-top_plate_link}"
HUSKY_LMS1XX_XYZ="${HUSKY_LMS1XX_XYZ:-0.2206 0.0 0.00635}"
HUSKY_LMS1XX_RPY="${HUSKY_LMS1XX_RPY:-0.0 0.0 0.0}"
HUSKY_LMS1XX_TOWER="${HUSKY_LMS1XX_TOWER:-1}"
HUSKY_LMS1XX_TOPIC="${HUSKY_LMS1XX_TOPIC:-front/scan}"

FORCE_REBUILD="${FORCE_REBUILD:-0}"

BUILD_ISOLATED_VOLUME="${BUILD_ISOLATED_VOLUME:-clearpath-husky-noetic-build-isolated}"
DEVEL_ISOLATED_VOLUME="${DEVEL_ISOLATED_VOLUME:-clearpath-husky-noetic-devel-isolated}"
INSTALL_ISOLATED_VOLUME="${INSTALL_ISOLATED_VOLUME:-clearpath-husky-noetic-install-isolated}"
LOG_ISOLATED_VOLUME="${LOG_ISOLATED_VOLUME:-clearpath-husky-noetic-log-isolated}"

BUILD_SIGNATURE_FILE="${CONTAINER_WS}/install_isolated/.src_ros1-build-signature"
LAUNCH_LOG="${CONTAINER_WS}/log/robot_bringup/robot-launch.log"

CONTAINER_STARTED=0

cleanup() {
    local exit_code=$?

    if [ "$CONTAINER_STARTED" -eq 1 ]; then
        echo
        echo "[INFO] Removing container: ${CONTAINER_NAME}"
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi

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
command -v sha256sum >/dev/null 2>&1 || error "sha256sum is required."

[ -d "$HOST_SRC" ] || error "ROS1 source directory does not exist: $HOST_SRC"
[ -f "$HOST_ROBOT_LAUNCH" ] || error "Merged launch file does not exist: $HOST_ROBOT_LAUNCH"

docker info >/dev/null 2>&1 || error "Docker is not accessible."
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
# Persistent isolated catkin volumes
# -----------------------------------------------------------------------------
echo "[INFO] Ensuring persistent catkin volumes exist."
docker volume create "$BUILD_ISOLATED_VOLUME" >/dev/null
docker volume create "$DEVEL_ISOLATED_VOLUME" >/dev/null
docker volume create "$INSTALL_ISOLATED_VOLUME" >/dev/null
docker volume create "$LOG_ISOLATED_VOLUME" >/dev/null

# -----------------------------------------------------------------------------
# Always start a fresh container.
# -----------------------------------------------------------------------------
echo "[INFO] Removing any previous container named: $CONTAINER_NAME"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

echo "[INFO] Starting fresh headless container: $CONTAINER_NAME"

docker run -d \
    --name "$CONTAINER_NAME" \
    --rm \
    --runtime nvidia \
    --privileged \
    --network host \
    --ipc host \
    -e NVIDIA_VISIBLE_DEVICES=all \
    -e NVIDIA_DRIVER_CAPABILITIES=all \
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
    -v "$HOST_SRC:$CONTAINER_SRC:rw" \
    -v "$BUILD_ISOLATED_VOLUME:${CONTAINER_WS}/build_isolated" \
    -v "$DEVEL_ISOLATED_VOLUME:${CONTAINER_WS}/devel_isolated" \
    -v "$INSTALL_ISOLATED_VOLUME:${CONTAINER_WS}/install_isolated" \
    -v "$LOG_ISOLATED_VOLUME:${CONTAINER_WS}/log" \
    -w "$CONTAINER_WS" \
    "$IMAGE_NAME" \
    sleep infinity >/dev/null

CONTAINER_STARTED=1

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
# Launch robot_bringup/robot.launch in the foreground.
#
# robot.launch contains:
#   - Husky hardware/control/teleop/diagnostics
#   - SICK LMS1xx
#   - hand_gesture_bridge/gesture_receiver.launch
#
# No Terminator or interactive shell is opened.
# -----------------------------------------------------------------------------
echo "[INFO] Launching: ${ROBOT_LAUNCH_PACKAGE}/${ROBOT_LAUNCH_FILE}"
echo "[INFO] ROS_MASTER_URI=$ROS_MASTER_URI_VALUE"
echo "[INFO] ROS_IP=$JETSON_IP"
echo "[INFO] Press Ctrl+C to stop the robot stack."

docker exec "$CONTAINER_NAME" /bin/bash -lc "
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

    roslaunch '${ROBOT_LAUNCH_PACKAGE}' '${ROBOT_LAUNCH_FILE}' \
        2>&1 | tee -a '${LAUNCH_LOG}'
"