import cv2
import mediapipe as mp

import rclpy
from rclpy.node import Node

from sensor_msgs.msg import Image
from cv_bridge import CvBridge

from mediapipe.tasks import python
from mediapipe.tasks.python import vision


HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17)
]


class HandLandmarkNode(Node):

    def __init__(self):
        super().__init__('hand_landmark_node')

        self.bridge = CvBridge()

        model_path = (
            '/root/ros2_ws/src/'
            'hand_gesture_recognition/models/hand_landmarker.task'
        )

        base_options = python.BaseOptions(
            model_asset_path=model_path
        )

        options = vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5
        )

        self.detector = vision.HandLandmarker.create_from_options(options)

        self.subscription = self.create_subscription(
            Image,
            '/zed/zed_node/rgb/color/rect/image',
            self.image_callback,
            1
        )

        self.get_logger().info('MediaPipe hand detector started')


    def image_callback(self, msg):

        frame = self.bridge.imgmsg_to_cv2(
            msg,
            desired_encoding='bgr8'
        )

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb
        )

        result = self.detector.detect(mp_image)

        height, width, _ = frame.shape

        for hand in result.hand_landmarks:

            points = []

            for landmark in hand:
                x = int(landmark.x * width)
                y = int(landmark.y * height)

                points.append((x, y))

                cv2.circle(
                    frame,
                    (x, y),
                    4,
                    (0, 255, 0),
                    -1
                )

            for start, end in HAND_CONNECTIONS:
                cv2.line(
                    frame,
                    points[start],
                    points[end],
                    (255, 255, 255),
                    2
                )

        cv2.imshow('ZED2 MediaPipe Hands', frame)
        cv2.waitKey(1)


def main(args=None):

    rclpy.init(args=args)

    node = HandLandmarkNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass

    cv2.destroyAllWindows()

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
