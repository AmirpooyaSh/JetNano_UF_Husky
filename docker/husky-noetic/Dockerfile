FROM arm64v8/ros:noetic

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg2 \
    lsb-release \
    usbutils \
    udev \
    && rm -rf /var/lib/apt/lists/*

# Clearpath package repository
RUN wget https://packages.clearpathrobotics.com/public.key -O - \
    | apt-key add - && \
    echo "deb https://packages.clearpathrobotics.com/stable/ubuntu focal main" \
    > /etc/apt/sources.list.d/clearpath-latest.list

# Husky ROS Noetic packages
RUN apt-get update && apt-get install -y \
    ros-noetic-husky-robot \
    ros-noetic-husky-tests \
    ros-noetic-joy \
    && rm -rf /var/lib/apt/lists/*

RUN echo "source /opt/ros/noetic/setup.bash" >> /root/.bashrc

WORKDIR /root

CMD ["bash"]
