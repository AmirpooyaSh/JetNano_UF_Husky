#!/usr/bin/env python3

import math
import os

import actionlib
import rospy
import yaml

from actionlib_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseWithCovarianceStamped
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from tf.transformations import quaternion_from_euler


DEFAULT_WAYPOINT_FILE = (
    "/catkin_ws/src/robot_bringup/config/waypoints.yaml"
)


def load_yaml(file_path):
    if not os.path.isfile(file_path):
        raise RuntimeError(
            "Waypoint file does not exist: {}".format(file_path)
        )

    with open(file_path, "r") as yaml_file:
        data = yaml.safe_load(yaml_file)

    if not isinstance(data, dict):
        raise RuntimeError("Waypoint YAML must contain a dictionary.")

    frame_id = str(data.get("frame_id", "")).strip()
    start_pose = data.get("start_pose")
    waypoints = data.get("waypoints")

    if not frame_id:
        raise RuntimeError("frame_id is missing.")

    if not isinstance(start_pose, dict):
        raise RuntimeError("start_pose is missing or invalid.")

    if not isinstance(waypoints, list):
        raise RuntimeError("waypoints must be a list.")

    validate_pose(start_pose, "start_pose")

    for index, waypoint in enumerate(waypoints):
        if not isinstance(waypoint, dict):
            raise RuntimeError(
                "Waypoint {} is invalid.".format(index + 1)
            )

        validate_pose(
            waypoint,
            waypoint.get("name", "waypoint_{}".format(index + 1)),
        )

    return frame_id, start_pose, waypoints


def validate_pose(pose, name):
    for field in ("x", "y", "yaw"):
        if field not in pose:
            raise RuntimeError(
                "{} is missing {}.".format(name, field)
            )

        try:
            value = float(pose[field])
        except (TypeError, ValueError):
            raise RuntimeError(
                "{} has an invalid {} value.".format(name, field)
            )

        if not math.isfinite(value):
            raise RuntimeError(
                "{} has a non-finite {} value.".format(name, field)
            )


def set_orientation(orientation, yaw):
    quaternion = quaternion_from_euler(0.0, 0.0, yaw)

    orientation.x = quaternion[0]
    orientation.y = quaternion[1]
    orientation.z = quaternion[2]
    orientation.w = quaternion[3]


def publish_initial_pose(publisher, frame_id, start_pose):
    message = PoseWithCovarianceStamped()

    message.header.stamp = rospy.Time.now()
    message.header.frame_id = frame_id

    message.pose.pose.position.x = float(start_pose["x"])
    message.pose.pose.position.y = float(start_pose["y"])
    message.pose.pose.position.z = 0.0

    set_orientation(
        message.pose.pose.orientation,
        float(start_pose["yaw"]),
    )

    publisher.publish(message)

    rospy.loginfo(
        "Published initial pose: x=%.3f, y=%.3f, yaw=%.3f",
        float(start_pose["x"]),
        float(start_pose["y"]),
        float(start_pose["yaw"]),
    )


def create_goal(frame_id, waypoint):
    goal = MoveBaseGoal()

    goal.target_pose.header.stamp = rospy.Time.now()
    goal.target_pose.header.frame_id = frame_id

    goal.target_pose.pose.position.x = float(waypoint["x"])
    goal.target_pose.pose.position.y = float(waypoint["y"])
    goal.target_pose.pose.position.z = 0.0

    set_orientation(
        goal.target_pose.pose.orientation,
        float(waypoint["yaw"]),
    )

    return goal


def main():
    rospy.init_node("yaml_waypoint_sender")

    waypoint_file = rospy.get_param(
        "~waypoint_file",
        DEFAULT_WAYPOINT_FILE,
    )

    frame_id, start_pose, waypoints = load_yaml(waypoint_file)

    rospy.loginfo("Loaded waypoint file: %s", waypoint_file)
    rospy.loginfo("Loaded %d waypoint(s).", len(waypoints))

    initial_pose_publisher = rospy.Publisher(
        "/initialpose",
        PoseWithCovarianceStamped,
        queue_size=1,
        latch=True,
    )

    move_base_client = actionlib.SimpleActionClient(
        "/move_base",
        MoveBaseAction,
    )

    rospy.loginfo("Waiting for /move_base...")
    move_base_client.wait_for_server()
    rospy.loginfo("/move_base is available.")

    rospy.on_shutdown(move_base_client.cancel_all_goals)

    publish_initial_pose(
        initial_pose_publisher,
        frame_id,
        start_pose,
    )

    rospy.sleep(2.0)

    for index, waypoint in enumerate(waypoints):
        if rospy.is_shutdown():
            break

        name = str(
            waypoint.get(
                "name",
                "waypoint_{}".format(index + 1),
            )
        )

        goal = create_goal(frame_id, waypoint)

        rospy.loginfo(
            "Sending %s: x=%.3f, y=%.3f, yaw=%.3f",
            name,
            float(waypoint["x"]),
            float(waypoint["y"]),
            float(waypoint["yaw"]),
        )

        move_base_client.send_goal(goal)
        move_base_client.wait_for_result()

        state = move_base_client.get_state()

        if state != GoalStatus.SUCCEEDED:
            rospy.logerr(
                "%s failed with action state %d: %s",
                name,
                state,
                move_base_client.get_goal_status_text(),
            )
            move_base_client.cancel_goal()
            return

        rospy.loginfo("%s reached successfully.", name)

    rospy.loginfo("Waypoint sequence completed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, yaml.YAMLError) as error:
        rospy.logfatal("%s", error)
    except rospy.ROSInterruptException:
        pass
