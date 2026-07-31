#!/usr/bin/env python3

import zmq
import rospy

from hand_gesture_bridge.msg import (
    HandGestureState,
    HandGestureStateArray
)


class GestureReceiver:

    def __init__(self):

        rospy.init_node("gesture_receiver")

        self.pub = rospy.Publisher(
            "/zed/hand_gesture_states",
            HandGestureStateArray,
            queue_size=10
        )

        context = zmq.Context()

        self.socket = context.socket(zmq.SUB)

        self.socket.connect(
            "tcp://127.0.0.1:5555"
        )

        self.socket.setsockopt_string(
            zmq.SUBSCRIBE,
            ""
        )

        rospy.loginfo(
            "Waiting for ROS2 gesture data on ZeroMQ port 5555..."
        )

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
                state.left_gesture_confidence = skeleton["left_confidence"]

                state.right_gesture = skeleton["right_gesture"]
                state.right_gesture_confidence = skeleton["right_confidence"]

                output.skeletons.append(state)

            self.pub.publish(output)


if __name__ == "__main__":

    receiver = GestureReceiver()
    receiver.run()
