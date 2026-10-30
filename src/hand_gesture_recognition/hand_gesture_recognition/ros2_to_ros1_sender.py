import json
import zmq

import rclpy
from rclpy.node import Node

from hand_gesture_msgs.msg import HandGestureStateArray
from visualization_msgs.msg import MarkerArray


# Gesture/position data (unchanged).
GESTURE_PORT = 5555
# Skeleton markers (joints + bones) from zed_skeleton_yolo.py.
# Separate port, so the gesture stream stays exactly as before.
SKELETON_PORT = 5556
SKELETON_TOPIC = '/zed/skeleton_markers'


class GestureSender(Node):

    def __init__(self):
        super().__init__('gesture_sender')

        context = zmq.Context()

        self.socket = context.socket(zmq.PUB)
        self.socket.bind("tcp://*:{}".format(GESTURE_PORT))

        self.skeleton_socket = context.socket(zmq.PUB)
        self.skeleton_socket.bind("tcp://*:{}".format(SKELETON_PORT))

        self.create_subscription(
            HandGestureStateArray,
            '/zed/hand_gesture_states',
            self.callback,
            10
        )

        self.create_subscription(
            MarkerArray,
            SKELETON_TOPIC,
            self.skeleton_callback,
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

    def skeleton_callback(self, msg):

        markers = []

        for m in msg.markers:

            markers.append({
                "frame_id": m.header.frame_id,
                "ns": m.ns,
                "id": m.id,
                "type": m.type,
                "action": m.action,

                "position": [
                    m.pose.position.x,
                    m.pose.position.y,
                    m.pose.position.z,
                ],
                "orientation": [
                    m.pose.orientation.x,
                    m.pose.orientation.y,
                    m.pose.orientation.z,
                    m.pose.orientation.w,
                ],
                "scale": [m.scale.x, m.scale.y, m.scale.z],
                "color": [m.color.r, m.color.g, m.color.b, m.color.a],

                "points": [[p.x, p.y, p.z] for p in m.points],
            })

        self.skeleton_socket.send_json({"markers": markers})


def main(args=None):

    rclpy.init(args=args)

    node = GestureSender()

    rclpy.spin(node)

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()