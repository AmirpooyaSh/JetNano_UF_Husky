import json
import zmq

import rclpy
from rclpy.node import Node

from hand_gesture_msgs.msg import HandGestureStateArray


class GestureSender(Node):

    def __init__(self):
        super().__init__('gesture_sender')

        context = zmq.Context()
        self.socket = context.socket(zmq.PUB)
        self.socket.bind("tcp://*:5555")

        self.create_subscription(
            HandGestureStateArray,
            '/zed/hand_gesture_states',
            self.callback,
            10
        )

    def callback(self, msg):

        data = {
            "frame_id": msg.header.frame_id,
            "skeletons": []
        }

        for s in msg.skeletons:

            data["skeletons"].append({
                "skeleton_index": s.skeleton_index,

                "x": s.position.x,
                "y": s.position.y,
                "z": s.position.z,

                "left_gesture": s.left_gesture,
                "left_confidence": s.left_gesture_confidence,

                "right_gesture": s.right_gesture,
                "right_confidence": s.right_gesture_confidence,
            })

        self.socket.send_json(data)


def main(args=None):

    rclpy.init(args=args)

    node = GestureSender()

    rclpy.spin(node)

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
