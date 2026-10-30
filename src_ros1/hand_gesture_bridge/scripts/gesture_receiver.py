#!/usr/bin/env python3

import math

import zmq
import rospy
import tf2_ros

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
# Each zone is a separate flat ring on the ground, so the zones never overlap.
ZONES = [
    ("stop",      0.0 * FEET_TO_METERS,  4.0 * FEET_TO_METERS, (1.0, 0.0, 0.0)),
    ("slow_down", 4.0 * FEET_TO_METERS, 12.0 * FEET_TO_METERS, (1.0, 1.0, 0.0)),
    ("proceed",  12.0 * FEET_TO_METERS, 20.0 * FEET_TO_METERS, (0.0, 1.0, 0.0)),
]

# Frame the zones are drawn in, and their height in it (m).
# Set ZONE_Z to the same value as MARKER_Z in robot_footprint_marker.py.
ZONE_FRAME = "base_link"
ZONE_Z = 0.0
ZONE_ALPHA = 0.3        # transparency (0 = invisible, 1 = solid)
ZONE_SEGMENTS = 64      # circle smoothness
ZONE_LIFETIME = 0.5     # s

# ================================================================
# Skeletons (joints + bones) forwarded from ROS2 /zed/skeleton_markers
# ================================================================

GESTURE_PORT = 5555
SKELETON_PORT = 5556
SKELETON_TOPIC = "/zed/skeleton_markers"
# Skeleton markers disappear if no update arrives for this long (s).
SKELETON_LIFETIME = 0.5


def flat_ring_triangles(inner_radius, outer_radius, segments):
    """
    Triangles (TRIANGLE_LIST) of a flat ring at z = 0, centered at (0, 0).
    With inner_radius = 0 it becomes a filled disc.
    """

    def p(x, y):
        return Point(x=x, y=y, z=0.0)

    points = []

    for i in range(segments):
        a0 = 2.0 * math.pi * i / segments
        a1 = 2.0 * math.pi * (i + 1) / segments

        c0, s0 = math.cos(a0), math.sin(a0)
        c1, s1 = math.cos(a1), math.sin(a1)

        o0, o1 = p(outer_radius * c0, outer_radius * s0), p(outer_radius * c1, outer_radius * s1)

        if inner_radius > 0.0:
            i0 = p(inner_radius * c0, inner_radius * s0)
            i1 = p(inner_radius * c1, inner_radius * s1)
            points += [i0, o0, o1]
            points += [i0, o1, i1]
        else:
            points += [p(0.0, 0.0), o0, o1]

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

        # TF: person position (camera frame) -> ZONE_FRAME.
        self.tf_buffer = tf2_ros.Buffer(cache_time=rospy.Duration(10.0))
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)

        # Ring geometry is the same every time, so build it once.
        self.zone_points = [
            flat_ring_triangles(inner, outer, ZONE_SEGMENTS)
            for _, inner, outer, _ in ZONES
        ]

        # Skeleton markers (joints + bones) from ROS2.
        self.skeleton_pub = rospy.Publisher(
            SKELETON_TOPIC,
            MarkerArray,
            queue_size=10
        )

        self.context = zmq.Context()

        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect("tcp://127.0.0.1:{}".format(GESTURE_PORT))
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "")

        self.skeleton_socket = self.context.socket(zmq.SUB)
        self.skeleton_socket.connect(
            "tcp://127.0.0.1:{}".format(SKELETON_PORT)
        )
        self.skeleton_socket.setsockopt_string(zmq.SUBSCRIBE, "")

        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)
        self.poller.register(self.skeleton_socket, zmq.POLLIN)

        rospy.loginfo(
            "Waiting for ROS2 gesture data on ZeroMQ port %d and skeleton "
            "markers on port %d...",
            GESTURE_PORT,
            SKELETON_PORT
        )

    def lookup_zone_transform(self, source_frame, stamp):
        """ZONE_FRAME <- source_frame at stamp (latest as fallback), or None."""
        for lookup_time in (stamp, rospy.Time(0)):
            try:
                return self.tf_buffer.lookup_transform(
                    ZONE_FRAME,
                    source_frame,
                    lookup_time,
                    rospy.Duration(0.05)
                )
            except (
                tf2_ros.LookupException,
                tf2_ros.ConnectivityException,
                tf2_ros.ExtrapolationException,
            ):
                continue
        return None

    @staticmethod
    def transform_point(transform, x, y, z):
        t = transform.transform.translation
        q = transform.transform.rotation
        xx, yy, zz = q.x * q.x, q.y * q.y, q.z * q.z
        xy, xz, yz = q.x * q.y, q.x * q.z, q.y * q.z
        wx, wy, wz = q.w * q.x, q.w * q.y, q.w * q.z
        rx = (1 - 2 * (yy + zz)) * x + 2 * (xy - wz) * y + 2 * (xz + wy) * z
        ry = 2 * (xy + wz) * x + (1 - 2 * (xx + zz)) * y + 2 * (yz - wx) * z
        return rx + t.x, ry + t.y

    def gesture_callback(self, msg):

        zones = MarkerArray()

        stamp = rospy.Time.now()

        source_frame = msg.header.frame_id or "zed_camera_link"
        transform = None
        if msg.skeletons:
            transform = self.lookup_zone_transform(source_frame, msg.header.stamp)
            if transform is None:
                rospy.logwarn_throttle(
                    5.0,
                    "ZONES: no %s -> %s transform; zones not drawn.",
                    source_frame,
                    ZONE_FRAME
                )
                return

        for skeleton in msg.skeletons:

            # Person position on the ground, in ZONE_FRAME.
            x, y = self.transform_point(
                transform,
                skeleton.position.x,
                skeleton.position.y,
                skeleton.position.z
            )

            for zone_index, (name, _, _, color) in enumerate(ZONES):

                marker = Marker()

                marker.header.stamp = stamp
                marker.header.frame_id = ZONE_FRAME

                marker.ns = "proxemic_zones"
                marker.id = skeleton.skeleton_index * len(ZONES) + zone_index

                marker.type = Marker.TRIANGLE_LIST
                marker.action = Marker.ADD

                marker.pose.position.x = x
                marker.pose.position.y = y
                marker.pose.position.z = ZONE_Z
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

    def publish_skeletons(self, data):

        output = MarkerArray()
        stamp = rospy.Time.now()

        for item in data.get("markers", []):

            marker = Marker()

            marker.header.stamp = stamp
            marker.header.frame_id = item.get("frame_id") or "zed_camera_link"

            marker.ns = item.get("ns", "")
            marker.id = int(item.get("id", 0))
            marker.type = int(item.get("type", Marker.SPHERE_LIST))
            marker.action = int(item.get("action", Marker.ADD))

            position = item.get("position", [0.0, 0.0, 0.0])
            orientation = item.get("orientation", [0.0, 0.0, 0.0, 1.0])
            marker.pose.position.x = position[0]
            marker.pose.position.y = position[1]
            marker.pose.position.z = position[2]
            marker.pose.orientation.x = orientation[0]
            marker.pose.orientation.y = orientation[1]
            marker.pose.orientation.z = orientation[2]
            marker.pose.orientation.w = orientation[3]

            scale = item.get("scale", [0.05, 0.05, 0.05])
            marker.scale.x = scale[0]
            marker.scale.y = scale[1]
            marker.scale.z = scale[2]

            color = item.get("color", [0.0, 1.0, 0.0, 1.0])
            marker.color.r = color[0]
            marker.color.g = color[1]
            marker.color.b = color[2]
            marker.color.a = color[3]

            marker.points = [
                Point(x=p[0], y=p[1], z=p[2])
                for p in item.get("points", [])
            ]

            if marker.action == Marker.ADD:
                marker.lifetime = rospy.Duration(SKELETON_LIFETIME)

            output.markers.append(marker)

        self.skeleton_pub.publish(output)

    def run(self):

        while not rospy.is_shutdown():

            events = dict(self.poller.poll(timeout=200))

            # Skeleton markers: read everything waiting, publish only the
            # newest, so this stream can never build up a delay.
            if self.skeleton_socket in events:
                latest = None
                while True:
                    try:
                        latest = self.skeleton_socket.recv_json(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                if latest is not None:
                    self.publish_skeletons(latest)

            # Gesture data: publish every waiting message, in order
            # (same content as before), without falling behind.
            if self.socket in events:
                while True:
                    try:
                        data = self.socket.recv_json(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                    self.publish_gestures(data)

    def publish_gestures(self, data):

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