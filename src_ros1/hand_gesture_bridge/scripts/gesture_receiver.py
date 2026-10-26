#!/usr/bin/env python3

import math

import zmq
import rospy

from geometry_msgs.msg import Point
from visualization_msgs.msg import Marker, MarkerArray

from hand_gesture_bridge.msg import (
    HandGestureState,
    HandGestureStateArray
)


# ================================================================
# Hall's proxemic zones drawn around each detected person
# ================================================================

FEET_TO_METERS = 0.3048

# (name, inner radius [m], outer radius [m], (r, g, b))
# Each zone is a separate ring volume, so the zones never overlap.
ZONES = [
    ("stop",      0.0 * FEET_TO_METERS,  4.0 * FEET_TO_METERS - 0.01, (1.0, 0.0, 0.0)),
    ("slow_down", 4.0 * FEET_TO_METERS + 0.01, 12.0 * FEET_TO_METERS - 0.01, (1.0, 1.0, 0.0)),
    ("proceed",  12.0 * FEET_TO_METERS + 0.01, 20.0 * FEET_TO_METERS, (0.0, 1.0, 0.0)),
]

ZONE_HEIGHT = 1.0       # m, same height as the previous box
ZONE_ALPHA = 0.3        # transparency (0 = invisible, 1 = solid)
ZONE_SEGMENTS = 64      # circle smoothness
ZONE_LIFETIME = 0.5     # s


def ring_triangles(inner_radius, outer_radius, height, segments):
    """
    Triangles (TRIANGLE_LIST) of a closed ring volume centered at (0, 0):
    top, bottom, outer wall and inner wall.
    With inner_radius = 0 it becomes a solid cylinder.
    """

    def p(x, y, z):
        return Point(x=x, y=y, z=z)

    points = []
    z0 = 0.0
    z1 = height

    for i in range(segments):
        a0 = 2.0 * math.pi * i / segments
        a1 = 2.0 * math.pi * (i + 1) / segments

        c0, s0 = math.cos(a0), math.sin(a0)
        c1, s1 = math.cos(a1), math.sin(a1)

        o0x, o0y = outer_radius * c0, outer_radius * s0
        o1x, o1y = outer_radius * c1, outer_radius * s1
        i0x, i0y = inner_radius * c0, inner_radius * s0
        i1x, i1y = inner_radius * c1, inner_radius * s1

        for z in (z0, z1):
            # Top / bottom face
            if inner_radius > 0.0:
                points += [p(i0x, i0y, z), p(o0x, o0y, z), p(o1x, o1y, z)]
                points += [p(i0x, i0y, z), p(o1x, o1y, z), p(i1x, i1y, z)]
            else:
                points += [p(0.0, 0.0, z), p(o0x, o0y, z), p(o1x, o1y, z)]

        # Outer wall
        points += [p(o0x, o0y, z0), p(o1x, o1y, z0), p(o1x, o1y, z1)]
        points += [p(o0x, o0y, z0), p(o1x, o1y, z1), p(o0x, o0y, z1)]

        # Inner wall
        if inner_radius > 0.0:
            points += [p(i0x, i0y, z0), p(i1x, i1y, z1), p(i1x, i1y, z0)]
            points += [p(i0x, i0y, z0), p(i0x, i0y, z1), p(i1x, i1y, z1)]

    return points


class GestureReceiver:

    def __init__(self):

        rospy.init_node("gesture_receiver")

        # Publishes converted ROS2 skeleton data as ROS1.
        self.gesture_pub = rospy.Publisher(
            "/zed/hand_gesture_states",
            HandGestureStateArray,
            queue_size=10
        )

        # Publishes proxemic zones generated from the ROS1 skeleton topic.
        # (Topic name kept so existing RViz configs still work.)
        self.box_pub = rospy.Publisher(
            "/zed/skeleton_boxes",
            MarkerArray,
            queue_size=10
        )

        # This same node subscribes to its ROS1 skeleton output.
        self.gesture_sub = rospy.Subscriber(
            "/zed/hand_gesture_states",
            HandGestureStateArray,
            self.gesture_callback,
            queue_size=10
        )

        # Ring geometry is the same every time, so build it once.
        self.zone_points = [
            ring_triangles(inner, outer, ZONE_HEIGHT, ZONE_SEGMENTS)
            for _, inner, outer, _ in ZONES
        ]

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect("tcp://127.0.0.1:5555")
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "")

        rospy.loginfo(
            "Waiting for ROS2 gesture data on ZeroMQ port 5555..."
        )

    def gesture_callback(self, msg):

        zones = MarkerArray()

        frame_id = "base_link"
        stamp = rospy.Time.now()

        for skeleton in msg.skeletons:

            for zone_index, (name, _, _, color) in enumerate(ZONES):

                marker = Marker()

                marker.header.stamp = stamp
                marker.header.frame_id = frame_id

                marker.ns = "proxemic_zones"
                marker.id = skeleton.skeleton_index * len(ZONES) + zone_index

                marker.type = Marker.TRIANGLE_LIST
                marker.action = Marker.ADD

                # Use only X and Y from the ROS1 skeleton message.
                marker.pose.position.x = skeleton.position.x
                marker.pose.position.y = skeleton.position.y
                marker.pose.position.z = 0.0
                marker.pose.orientation.w = 1.0

                # TRIANGLE_LIST points are already in meters.
                marker.scale.x = 1.0
                marker.scale.y = 1.0
                marker.scale.z = 1.0

                marker.color.r = color[0]
                marker.color.g = color[1]
                marker.color.b = color[2]
                marker.color.a = ZONE_ALPHA

                marker.points = self.zone_points[zone_index]

                marker.lifetime = rospy.Duration(ZONE_LIFETIME)

                zones.markers.append(marker)

        self.box_pub.publish(zones)

    def run(self):

        while not rospy.is_shutdown():

            data = self.socket.recv_json()

            output = HandGestureStateArray()

            output.header.stamp = rospy.Time.now()
            output.header.frame_id = data.get(
                "frame_id",
                "zed_camera_link"
            )

            for skeleton in data.get("skeletons", []):

                state = HandGestureState()

                state.skeleton_index = skeleton["skeleton_index"]

                state.position.x = skeleton["x"]
                state.position.y = skeleton["y"]
                state.position.z = skeleton["z"]

                state.left_gesture = skeleton["left_gesture"]
                state.left_gesture_confidence = skeleton[
                    "left_confidence"
                ]

                state.right_gesture = skeleton["right_gesture"]
                state.right_gesture_confidence = skeleton[
                    "right_confidence"
                ]

                output.skeletons.append(state)

            self.gesture_pub.publish(output)


if __name__ == "__main__":

    receiver = GestureReceiver()
    receiver.run()