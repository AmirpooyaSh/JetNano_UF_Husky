# ================================================================
# ZED2 + ROS 2 Humble development container
#
# Host:
#   JetPack 6.2.2 / Ubuntu 22.04
#
# No ZED ROS package is cloned or built here.
# The host source:
#   ~/catkin_ws/src/zed-ros2-wrapper
# is bind-mounted at runtime to:
#   /root/ros2_ws/src/zed-ros2-wrapper
# ================================================================

FROM stereolabs/zed:5.4.1-devel-jetson-jp6.2.2

ARG DEBIAN_FRONTEND=noninteractive

ENV ROS_DISTRO=humble
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
ENV NVIDIA_DRIVER_CAPABILITIES=all

RUN apt-get update && apt-get install -y --no-install-recommends \
    locales \
    curl \
    wget \
    gnupg2 \
    lsb-release \
    ca-certificates \
    software-properties-common \
    build-essential \
    cmake \
    git \
    jq \
    less \
    usbutils \
    python3 \
    python3-dev \
    python3-pip \
    python3-wheel \
    terminator \
    dbus-x11 \
    && locale-gen en_US.UTF-8 \
    && update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    && add-apt-repository universe \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /usr/share/keyrings && \
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    | gpg --dearmor \
    -o /usr/share/keyrings/ros-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
    http://packages.ros.org/ros2/ubuntu jammy main" \
    > /etc/apt/sources.list.d/ros2.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-ros-base \
    ros-humble-rviz2 \
    python3-rosdep \
    python3-colcon-common-extensions \
    python3-vcstool \
    && rm -rf /var/lib/apt/lists/*

RUN rosdep init || true
RUN rosdep update --rosdistro humble

RUN mkdir -p /root/ros2_ws/src
WORKDIR /root/ros2_ws

# Every new Bash terminal inside Terminator will source ROS automatically.
RUN printf '%s\n' \
    'source /opt/ros/humble/setup.bash' \
    '[ -f /root/ros2_ws/install/setup.bash ] && source /root/ros2_ws/install/setup.bash' \
    >> /root/.bashrc

RUN printf '%s\n' \
    '#!/bin/bash' \
    'set -e' \
    'source /opt/ros/humble/setup.bash' \
    '[ -f /root/ros2_ws/install/setup.bash ] && source /root/ros2_ws/install/setup.bash' \
    'exec "$@"' \
    > /ros_entrypoint.sh && \
    chmod +x /ros_entrypoint.sh

# MediaPipe + OpenCV support for ROS2 image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-cv-bridge \
    python3-opencv \
    && python3 -m pip install --no-cache-dir \
        "numpy<2" \
        mediapipe-nightly==0.10.36rc20260527 \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]