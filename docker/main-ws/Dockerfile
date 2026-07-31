# ================================================================
# main-ws
# ARM64 / Ubuntu 20.04 / ROS Noetic
# CUDA 12.6 runtime + development libraries + cuDNN 9
#
# Intended host:
#   NVIDIA Jetson / JetPack 6.2.2
#
# Base ROS image:
#   arm64v8/ros:noetic
# ================================================================

FROM arm64v8/ros:noetic

ARG DEBIAN_FRONTEND=noninteractive

ENV ROS_DISTRO=noetic
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# ARM64 CUDA repository architecture
ENV NVARCH=sbsa

# ---------------------------------------------------------------
# CUDA versions
# ---------------------------------------------------------------
ENV CUDA_VERSION=12.6.0
ENV NV_CUDA_CUDART_VERSION=12.6.37-1
ENV NV_CUDA_LIB_VERSION=12.6.0-1

ENV NV_CUDA_CUDART_DEV_VERSION=12.6.37-1
ENV NV_NVML_DEV_VERSION=12.6.37-1

ENV NV_NVTX_VERSION=12.6.37-1

ENV NV_LIBNPP_VERSION=12.3.1.23-1
ENV NV_LIBNPP_DEV_VERSION=12.3.1.23-1

ENV NV_LIBCUSPARSE_VERSION=12.5.2.23-1
ENV NV_LIBCUSPARSE_DEV_VERSION=12.5.2.23-1

ENV NV_LIBCUBLAS_PACKAGE_NAME=libcublas-12-6
ENV NV_LIBCUBLAS_VERSION=12.6.0.22-1

ENV NV_LIBCUBLAS_DEV_PACKAGE_NAME=libcublas-dev-12-6
ENV NV_LIBCUBLAS_DEV_VERSION=12.6.0.22-1

ENV NV_LIBNCCL_PACKAGE_NAME=libnccl2
ENV NV_LIBNCCL_DEV_PACKAGE_NAME=libnccl-dev
ENV NV_LIBNCCL_PACKAGE_VERSION=2.22.3-1
ENV NCCL_VERSION=2.22.3-1

ENV NV_CUDA_NSIGHT_COMPUTE_VERSION=12.6.0-1

# ---------------------------------------------------------------
# cuDNN
# ---------------------------------------------------------------
ENV NV_CUDNN_VERSION=9.3.0.75-1
ENV NV_CUDNN_PACKAGE_NAME=libcudnn9-cuda-12

# ---------------------------------------------------------------
# Basic development / runtime tools
# ---------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg2 \
    lsb-release \
    build-essential \
    cmake \
    git \
    wget \
    nano \
    vim \
    less \
    htop \
    usbutils \
    pciutils \
    udev \
    x11-apps \
    mesa-utils \
    xauth \
    terminator \
    python3 \
    python3-pip \
    python3-dev \
    python3-setuptools \
    python3-wheel \
    python3-requests \
    python3-zmq \
    python3-rosdep \
    python3-rosinstall \
    python3-vcstools \
    python3-catkin-tools \
    python3-osrf-pycommon \
    alsa-utils \
    pulseaudio-utils \
    libasound2-dev \
    libtinfo5 \
    libncursesw5 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------
# NVIDIA CUDA 12.6 repository for Ubuntu 20.04 ARM64/SBSA
# ---------------------------------------------------------------
RUN curl -fsSL \
    https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/${NVARCH}/3bf863cc.pub \
    | apt-key add - && \
    echo "deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/${NVARCH} /" \
    > /etc/apt/sources.list.d/cuda.list

# ---------------------------------------------------------------
# CUDA base/runtime
# ---------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    cuda-cudart-12-6=${NV_CUDA_CUDART_VERSION} \
    cuda-compat-12-6 \
    cuda-libraries-12-6=${NV_CUDA_LIB_VERSION} \
    libnpp-12-6=${NV_LIBNPP_VERSION} \
    cuda-nvtx-12-6=${NV_NVTX_VERSION} \
    libcusparse-12-6=${NV_LIBCUSPARSE_VERSION} \
    ${NV_LIBCUBLAS_PACKAGE_NAME}=${NV_LIBCUBLAS_VERSION} \
    ${NV_LIBNCCL_PACKAGE_NAME}=${NV_LIBNCCL_PACKAGE_VERSION}+cuda12.6 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------
# CUDA development stack
# ---------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    cuda-cudart-dev-12-6=${NV_CUDA_CUDART_DEV_VERSION} \
    cuda-command-line-tools-12-6=${NV_CUDA_LIB_VERSION} \
    cuda-minimal-build-12-6=${NV_CUDA_LIB_VERSION} \
    cuda-libraries-dev-12-6=${NV_CUDA_LIB_VERSION} \
    cuda-nvml-dev-12-6=${NV_NVML_DEV_VERSION} \
    libnpp-dev-12-6=${NV_LIBNPP_DEV_VERSION} \
    libcusparse-dev-12-6=${NV_LIBCUSPARSE_DEV_VERSION} \
    ${NV_LIBCUBLAS_DEV_PACKAGE_NAME}=${NV_LIBCUBLAS_DEV_VERSION} \
    ${NV_LIBNCCL_DEV_PACKAGE_NAME}=${NV_LIBNCCL_PACKAGE_VERSION}+cuda12.6 \
    cuda-nsight-compute-12-6=${NV_CUDA_NSIGHT_COMPUTE_VERSION} \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------
# cuDNN runtime + development libraries
# ---------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ${NV_CUDNN_PACKAGE_NAME}=${NV_CUDNN_VERSION} \
    libcudnn9-dev-cuda-12=${NV_CUDNN_VERSION} \
    && rm -rf /var/lib/apt/lists/*

# Prevent accidental upgrades of explicitly pinned GPU libraries
RUN apt-mark hold \
    ${NV_LIBCUBLAS_PACKAGE_NAME} \
    ${NV_LIBCUBLAS_DEV_PACKAGE_NAME} \
    ${NV_LIBNCCL_PACKAGE_NAME} \
    ${NV_LIBNCCL_DEV_PACKAGE_NAME} \
    ${NV_CUDNN_PACKAGE_NAME}

# ---------------------------------------------------------------
# NVIDIA runtime environment
# ---------------------------------------------------------------
RUN echo "/usr/local/nvidia/lib" >> /etc/ld.so.conf.d/nvidia.conf && \
    echo "/usr/local/nvidia/lib64" >> /etc/ld.so.conf.d/nvidia.conf

ENV PATH=/usr/local/nvidia/bin:/usr/local/cuda/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64
ENV LIBRARY_PATH=/usr/local/cuda/lib64/stubs

ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# ---------------------------------------------------------------
# rosdep
# ---------------------------------------------------------------
RUN rosdep init || true && \
    rosdep update --rosdistro noetic

# ---------------------------------------------------------------
# Catkin workspace
# ---------------------------------------------------------------
RUN mkdir -p /catkin_ws/src
WORKDIR /catkin_ws

RUN /bin/bash -c "source /opt/ros/noetic/setup.bash && \
    catkin init && \
    catkin config --extend /opt/ros/noetic"

# ---------------------------------------------------------------
# Shell setup
# ---------------------------------------------------------------
RUN echo "source /opt/ros/noetic/setup.bash" >> /root/.bashrc && \
    echo "if [ -f /catkin_ws/devel/setup.bash ]; then source /catkin_ws/devel/setup.bash; fi" >> /root/.bashrc && \
    echo "cd /catkin_ws" >> /root/.bashrc

# ---------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------
RUN printf '%s\n' \
    '#!/bin/bash' \
    'set -e' \
    'source /opt/ros/noetic/setup.bash' \
    'if [ -f /catkin_ws/devel/setup.bash ]; then source /catkin_ws/devel/setup.bash; fi' \
    'exec "$@"' \
    > /ros_entrypoint.sh && \
    chmod +x /ros_entrypoint.sh

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]