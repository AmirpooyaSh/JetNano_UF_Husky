#!/usr/bin/env python3
"""
Publishes the Husky footprint as a bright red rectangle outline on the ground
for RViz.

Rectangle: 1.00 m long (robot +x) x 0.66 m wide, centered on base_link.

Topic: /robot_footprint_marker  (visualization_msgs/MarkerArray)
"""

import rospy
from geometry_msgs.msg import Point
from visualization_msgs.msg import Marker, MarkerArray


TOPIC = "/robot_footprint_marker"
FRAME_ID = "base_link"

ROBOT_LENGTH = 1.20     # m, along +x
ROBOT_WIDTH = 0.86      # m, along +y
MARKER_Z = 0.0          # m, height of the outline in FRAME_ID
LINE_WIDTH = 0.1       # m
PUBLISH_RATE = 10.0     # Hz


def footprint_marker():
    half_l = ROBOT_LENGTH / 2.0
    half_w = ROBOT_WIDTH / 2.0

    marker = Marker()
    marker.header.frame_id = FRAME_ID
    marker.ns = "robot_footprint"
    marker.id = 0
    marker.type = Marker.LINE_STRIP
    marker.action = Marker.ADD
    marker.pose.orientation.w = 1.0

    marker.scale.x = LINE_WIDTH

    # Bright red, opaque.
    marker.color.r = 1.0
    marker.color.g = 0.0
    marker.color.b = 0.0
    marker.color.a = 1.0

    # Closed outline: front-left, front-right, rear-right, rear-left, front-left.
    for x, y in (
        (half_l, half_w),
        (half_l, -half_w),
        (-half_l, -half_w),
        (-half_l, half_w),
        (half_l, half_w),
    ):
        marker.points.append(Point(x=x, y=y, z=MARKER_Z))

    return marker


def main():
    rospy.init_node("robot_footprint_marker")

    publisher = rospy.Publisher(TOPIC, MarkerArray, queue_size=1)
    marker = footprint_marker()
    rate = rospy.Rate(PUBLISH_RATE)

    rospy.loginfo(
        "Publishing robot footprint (%.2f x %.2f m) on %s in %s",
        ROBOT_LENGTH,
        ROBOT_WIDTH,
        TOPIC,
        FRAME_ID,
    )

    while not rospy.is_shutdown():
        marker.header.stamp = rospy.Time.now()
        markers = MarkerArray()
        markers.markers.append(marker)
        publisher.publish(markers)
        rate.sleep()


if __name__ == "__main__":
    main()