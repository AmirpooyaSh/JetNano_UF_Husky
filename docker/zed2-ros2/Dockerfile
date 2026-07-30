# ================================================================
# ZED2 + ROS 2 Humble on NVIDIA Jetson
#
# Host:
#   JetPack 6.2.2 / L4T R36.5 / Ubuntu 22.04
#
# Container:
#   StereoLabs ZED SDK 5.4.1
#   ROS 2 Humble
#   Current ZED ROS 2 Wrapper
# ================================================================

FROM stereolabs/zed:5.4.1-devel-jetson-jp6.2.2

ARG DEBIAN_FRONTEND=noninteractive

ENV ROS_DISTRO=humble
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
ENV NVIDIA_DRIVER_CAPABILITIES=all

# ------------------------------------------------
# Basic build/runtime tools
# ------------------------------------------------

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
    && locale-gen en_US.UTF-8 \
    && update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    && add-apt-repository universe \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------
# ROS 2 Humble repository
# ------------------------------------------------

RUN mkdir -p /usr/share/keyrings && \
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    | gpg --dearmor \
    -o /usr/share/keyrings/ros-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu jammy main" \
    > /etc/apt/sources.list.d/ros2.list

# ------------------------------------------------
# ROS 2 Humble + build tools
# ------------------------------------------------

RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-ros-base \
    python3-rosdep \
    python3-colcon-common-extensions \
    python3-vcstool \
    python3-rosinstall \
    python3-rosinstall-generator \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------
# rosdep
# ------------------------------------------------

RUN rosdep init || true
RUN rosdep update --rosdistro humble

# ------------------------------------------------
# ZED ROS 2 Wrapper workspace
# ------------------------------------------------

WORKDIR /root/ros2_ws/src

RUN git clone \
    --depth 1 \
    https://github.com/stereolabs/zed-ros2-wrapper.git

WORKDIR /root/ros2_ws

# Keep the NVIDIA/StereoLabs OpenCV stack from being replaced
# by Ubuntu's generic OpenCV packages during rosdep installation.
RUN apt-mark hold \
    libopencv-dev \
    nvidia-opencv-dev \
    nvidia-opencv \
    libopencv-python \
    libopencv-samples \
    opencv-licenses \
    opencv-samples-data \
    2>/dev/null || true

# Install ROS/package dependencies.
RUN apt-get update && \
    /bin/bash -c "source /opt/ros/humble/setup.bash && \
    rosdep install \
      --from-paths src \
      --ignore-src \
      --rosdistro humble \
      --skip-keys='libopencv-dev' \
      -r -y" && \
    rm -rf /var/lib/apt/lists/*

# ------------------------------------------------
# Build ZED ROS 2 Wrapper
# ------------------------------------------------

RUN /bin/bash -c ' \
    source /opt/ros/humble/setup.bash && \
    OPENCV_CMAKE_DIR="$(find /usr /opt -maxdepth 6 -iname OpenCVConfig.cmake 2>/dev/null | head -n 1 | xargs -r dirname)" && \
    CMAKE_ARGS="-DCMAKE_BUILD_TYPE=Release -DCMAKE_LIBRARY_PATH=/usr/local/cuda/lib64/stubs -DCMAKE_CXX_FLAGS=-Wl,--allow-shlib-undefined" && \
    if [ -n "$OPENCV_CMAKE_DIR" ]; then \
      CMAKE_ARGS="$CMAKE_ARGS -DOpenCV_DIR=$OPENCV_CMAKE_DIR"; \
    fi && \
    colcon build \
      --symlink-install \
      --packages-skip zed_debug \
      --parallel-workers 2 \
      --cmake-args $CMAKE_ARGS \
    '

# ------------------------------------------------
# ROS entrypoint
# ------------------------------------------------

RUN printf '%s\n' \
    '#!/bin/bash' \
    'set -e' \
    'source /opt/ros/humble/setup.bash' \
    'source /root/ros2_ws/install/local_setup.bash' \
    'exec "$@"' \
    > /ros_entrypoint.sh && \
    chmod +x /ros_entrypoint.sh

WORKDIR /root/ros2_ws

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
