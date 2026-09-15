#!/usr/bin/env python3

import zmq
import rospy

from visualization_msgs.msg import Marker, MarkerArray

from hand_gesture_bridge.msg import (
    HandGestureState,
    HandGestureStateArray
)


class GestureReceiver:

    def __init__(self):

        rospy.init_node("gesture_receiver")

        # Publishes converted ROS2 skeleton data as ROS1.
        self.gesture_pub = rospy.Publisher(
            "/zed/hand_gesture_states",
            HandGestureStateArray,
            queue_size=10
        )

        # Publishes boxes generated from the ROS1 skeleton topic.
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

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect("tcp://127.0.0.1:5555")
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "")

        rospy.loginfo(
            "Waiting for ROS2 gesture data on ZeroMQ port 5555..."
        )

    def gesture_callback(self, msg):

        boxes = MarkerArray()

        frame_id = "base_link"
        stamp = rospy.Time.now()

        for skeleton in msg.skeletons:

            box = Marker()

            box.header.stamp = stamp
            box.header.frame_id = frame_id

            box.ns = "skeleton_locations"
            box.id = skeleton.skeleton_index

            box.type = Marker.CUBE
            box.action = Marker.ADD

            # Use only X and Y from the ROS1 skeleton message.
            box.pose.position.x = skeleton.position.x
            box.pose.position.y = skeleton.position.y
            box.pose.position.z = 0.0
            box.pose.orientation.w = 1.0

            box.scale.x = 0.4
            box.scale.y = 0.4
            box.scale.z = 1.0

            box.color.r = 1.0
            box.color.g = 0.0
            box.color.b = 0.0
            box.color.a = 0.8

            box.lifetime = rospy.Duration(0.5)

            boxes.markers.append(box)

        self.box_pub.publish(boxes)

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