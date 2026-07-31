import os

from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource

from launch_ros.actions import Node

from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    # ZED wrapper launch file
    zed_launch = os.path.join(
        get_package_share_directory('zed_wrapper'),
        'launch',
        'zed_camera.launch.py'
    )

    start_zed2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(zed_launch),
        launch_arguments={
            'camera_model': 'zed2',
        }.items()
    )

    # Our skeleton visualization node
    skeleton_visualizer = Node(
        package='hand_gesture_recognition',
        executable='zed_skeleton_visualizer',
        name='zed_skeleton_visualizer',
        output='screen'
    )

    return LaunchDescription([
        start_zed2,
        skeleton_visualizer,
    ])
