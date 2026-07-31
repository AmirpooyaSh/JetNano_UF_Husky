import math
import cv2
import numpy as np
import mediapipe as mp

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from sensor_msgs.msg import Image
from visualization_msgs.msg import Marker, MarkerArray
from geometry_msgs.msg import Point
from cv_bridge import CvBridge

from zed_msgs.msg import ObjectsStamped

from hand_gesture_msgs.msg import (
    HandGestureState,
    HandGestureStateArray,
)

from mediapipe.tasks import python
from mediapipe.tasks.python import vision


# ================================================================
# ZED BODY_18
# ================================================================

RIGHT_ELBOW = 3
RIGHT_WRIST = 4

LEFT_ELBOW = 6
LEFT_WRIST = 7

BODY_18_BONES = [
    (0, 1),

    (1, 2), (2, 3), (3, 4),
    (1, 5), (5, 6), (6, 7),

    (1, 8), (8, 9), (9, 10),
    (1, 11), (11, 12), (12, 13),

    (0, 14), (14, 16),
    (0, 15), (15, 17),
]


FRAME_ID = 'zed_camera_link'


class ZedSkeletonVisualizer(Node):

    def __init__(self):

        super().__init__('zed_skeleton_visualizer')

        self.bridge = CvBridge()

        self.latest_image = None
        self.latest_image_header = None

        # Size of hand crop in pixels
        self.hand_crop_size = 300

        # Minimum MediaPipe gesture confidence
        self.gesture_threshold = 0.50

        # --------------------------------------------------------
        # MediaPipe Gesture Recognizer
        # --------------------------------------------------------

        model_path = (
            '/root/ros2_ws/src/'
            'hand_gesture_recognition/'
            'models/gesture_recognizer.task'
        )

        base_options = python.BaseOptions(
            model_asset_path=model_path
        )

        options = vision.GestureRecognizerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_hands=1,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        self.gesture_recognizer = (
            vision.GestureRecognizer.create_from_options(options)
        )

        # --------------------------------------------------------
        # Subscribers
        # --------------------------------------------------------

        self.image_sub = self.create_subscription(
            Image,
            '/zed/zed_node/rgb/color/rect/image',
            self.image_callback,
            qos_profile_sensor_data
        )

        self.skeleton_sub = self.create_subscription(
            ObjectsStamped,
            '/zed/zed_node/body_trk/skeletons',
            self.skeleton_callback,
            qos_profile_sensor_data
        )

        # --------------------------------------------------------
        # Publishers
        # --------------------------------------------------------

        # Existing BODY_18 visualization
        self.skeleton_marker_pub = self.create_publisher(
            MarkerArray,
            '/zed/skeleton_markers',
            10
        )

        # 2D image + skeleton + hand crops
        self.image_pub = self.create_publisher(
            Image,
            '/zed/skeleton_image',
            10
        )

        # Hand cubes + gesture labels
        self.hand_marker_pub = self.create_publisher(
            MarkerArray,
            '/zed/hand_gesture_markers',
            10
        )

        # Structured skeleton/gesture information
        self.state_pub = self.create_publisher(
            HandGestureStateArray,
            '/zed/hand_gesture_states',
            10
        )

        self.get_logger().info(
            'ZED skeleton + MediaPipe gesture node started'
        )

    # ============================================================
    # IMAGE
    # ============================================================

    def image_callback(self, msg):

        self.latest_image = self.bridge.imgmsg_to_cv2(
            msg,
            desired_encoding='bgr8'
        )

        self.latest_image_header = msg.header

    # ============================================================
    # VALIDATION
    # ============================================================

    def valid_2d(self, kp):

        x, y = kp

        return (
            math.isfinite(x) and
            math.isfinite(y) and
            x > 0 and
            y > 0
        )

    def valid_3d(self, kp):

        x, y, z = kp

        return (
            math.isfinite(x) and
            math.isfinite(y) and
            math.isfinite(z) and
            not (x == 0.0 and y == 0.0 and z == 0.0)
        )

    # ============================================================
    # HAND CROP
    # ============================================================

    def crop_hand(self, image, wrist, elbow):

        if image is None:
            return None, None

        if not self.valid_2d(wrist):
            return None, None

        wx = float(wrist[0])
        wy = float(wrist[1])

        # Move the crop slightly beyond the wrist in the
        # elbow -> wrist direction so fingers are more likely
        # to be inside the crop.
        cx = wx
        cy = wy

        if self.valid_2d(elbow):

            ex = float(elbow[0])
            ey = float(elbow[1])

            cx += 0.30 * (wx - ex)
            cy += 0.30 * (wy - ey)

        half = self.hand_crop_size // 2

        h, w = image.shape[:2]

        x1 = max(0, int(cx - half))
        y1 = max(0, int(cy - half))

        x2 = min(w, int(cx + half))
        y2 = min(h, int(cy + half))

        if x2 <= x1 or y2 <= y1:
            return None, None

        crop = image[y1:y2, x1:x2].copy()

        if crop.size == 0:
            return None, None

        return crop, (x1, y1, x2, y2)

    # ============================================================
    # MEDIAPIPE CLASSIFICATION
    # ============================================================

    def classify_hand(self, crop):

        if crop is None:
            return 'Null', 0.0

        rgb = cv2.cvtColor(
            crop,
            cv2.COLOR_BGR2RGB
        )

        rgb = np.ascontiguousarray(rgb)

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb
        )

        result = self.gesture_recognizer.recognize(mp_image)

        if not result.gestures or not result.gestures[0]:
            return 'Null', 0.0

        category = result.gestures[0][0]

        gesture = category.category_name
        confidence = float(category.score)

        if gesture is None or gesture == 'None':
            return 'Null', confidence

        if confidence < self.gesture_threshold:
            return 'Null', confidence

        return gesture, confidence

    # ============================================================
    # CENTROID
    # ============================================================

    def calculate_body_center(self, kp3d):

        valid_points = [
            kp for kp in kp3d
            if self.valid_3d(kp)
        ]

        if not valid_points:
            return None

        point = Point()

        point.x = sum(p[0] for p in valid_points) / len(valid_points)
        point.y = sum(p[1] for p in valid_points) / len(valid_points)
        point.z = sum(p[2] for p in valid_points) / len(valid_points)

        return point

    # ============================================================
    # HAND MARKERS
    # ============================================================

    def add_hand_marker(
        self,
        marker_array,
        marker_id,
        stamp,
        wrist,
        gesture,
        body_index,
        side
    ):

        # --------------------------------------------------------
        # Cube
        # --------------------------------------------------------

        cube = Marker()

        cube.header.stamp = stamp
        cube.header.frame_id = FRAME_ID

        cube.ns = f'body_{body_index}_{side}_hand'
        cube.id = marker_id

        cube.type = Marker.CUBE
        cube.action = Marker.ADD

        cube.pose.position.x = float(wrist[0])
        cube.pose.position.y = float(wrist[1])
        cube.pose.position.z = float(wrist[2])

        cube.pose.orientation.w = 1.0

        cube.scale.x = 0.18
        cube.scale.y = 0.18
        cube.scale.z = 0.18

        if side == 'left':
            cube.color.r = 0.0
            cube.color.g = 1.0
            cube.color.b = 0.0
        else:
            cube.color.r = 0.0
            cube.color.g = 0.5
            cube.color.b = 1.0

        cube.color.a = 0.45

        marker_array.markers.append(cube)

        # --------------------------------------------------------
        # Text
        # --------------------------------------------------------

        text = Marker()

        text.header.stamp = stamp
        text.header.frame_id = FRAME_ID

        text.ns = f'body_{body_index}_{side}_gesture'
        text.id = marker_id + 1

        text.type = Marker.TEXT_VIEW_FACING
        text.action = Marker.ADD

        text.pose.position.x = float(wrist[0])
        text.pose.position.y = float(wrist[1])
        text.pose.position.z = float(wrist[2]) + 0.20

        text.pose.orientation.w = 1.0

        text.scale.z = 0.09

        text.color.r = 1.0
        text.color.g = 1.0
        text.color.b = 1.0
        text.color.a = 1.0

        text.text = f'{side}: {gesture}'

        marker_array.markers.append(text)

    # ============================================================
    # SKELETON CALLBACK
    # ============================================================

    def skeleton_callback(self, msg):

        skeleton_markers = MarkerArray()
        hand_markers = MarkerArray()

        # Clear old RViz markers
        delete1 = Marker()
        delete1.action = Marker.DELETEALL
        skeleton_markers.markers.append(delete1)

        delete2 = Marker()
        delete2.action = Marker.DELETEALL
        hand_markers.markers.append(delete2)

        # Structured output
        states = HandGestureStateArray()

        states.header.stamp = msg.header.stamp
        states.header.frame_id = FRAME_ID

        # Latest ZED image
        image = None

        if self.latest_image is not None:
            image = self.latest_image.copy()

        skeleton_marker_id = 0
        hand_marker_id = 0

        # ========================================================
        # PEOPLE
        # ========================================================

        for body_index, body in enumerate(msg.objects):

            if not body.skeleton_available:
                continue

            kp2d = [
                kp.kp
                for kp in body.skeleton_2d.keypoints[:18]
            ]

            kp3d = [
                kp.kp
                for kp in body.skeleton_3d.keypoints[:18]
            ]

            if len(kp2d) < 18 or len(kp3d) < 18:
                continue

            # ====================================================
            # 3D SKELETON
            # ====================================================

            joints = Marker()

            joints.header.stamp = msg.header.stamp
            joints.header.frame_id = FRAME_ID

            joints.ns = f'body_{body_index}_joints'
            joints.id = skeleton_marker_id
            skeleton_marker_id += 1

            joints.type = Marker.SPHERE_LIST
            joints.action = Marker.ADD

            joints.pose.orientation.w = 1.0

            joints.scale.x = 0.05
            joints.scale.y = 0.05
            joints.scale.z = 0.05

            joints.color.g = 1.0
            joints.color.a = 1.0

            for kp in kp3d:

                if not self.valid_3d(kp):
                    continue

                p = Point()

                p.x = float(kp[0])
                p.y = float(kp[1])
                p.z = float(kp[2])

                joints.points.append(p)

            skeleton_markers.markers.append(joints)

            bones = Marker()

            bones.header.stamp = msg.header.stamp
            bones.header.frame_id = FRAME_ID

            bones.ns = f'body_{body_index}_bones'
            bones.id = skeleton_marker_id
            skeleton_marker_id += 1

            bones.type = Marker.LINE_LIST
            bones.action = Marker.ADD

            bones.pose.orientation.w = 1.0

            bones.scale.x = 0.025

            bones.color.g = 1.0
            bones.color.a = 1.0

            for start, end in BODY_18_BONES:

                p1 = kp3d[start]
                p2 = kp3d[end]

                if not self.valid_3d(p1):
                    continue

                if not self.valid_3d(p2):
                    continue

                point1 = Point()
                point1.x = float(p1[0])
                point1.y = float(p1[1])
                point1.z = float(p1[2])

                point2 = Point()
                point2.x = float(p2[0])
                point2.y = float(p2[1])
                point2.z = float(p2[2])

                bones.points.append(point1)
                bones.points.append(point2)

            skeleton_markers.markers.append(bones)

            # ====================================================
            # HAND AVAILABILITY
            # ====================================================

            left_available = (
                self.valid_2d(kp2d[LEFT_WRIST]) and
                self.valid_3d(kp3d[LEFT_WRIST])
            )

            right_available = (
                self.valid_2d(kp2d[RIGHT_WRIST]) and
                self.valid_3d(kp3d[RIGHT_WRIST])
            )

            left_gesture = 'N-A'
            left_confidence = 0.0

            right_gesture = 'N-A'
            right_confidence = 0.0

            left_box = None
            right_box = None

            # ====================================================
            # LEFT HAND
            # ====================================================

            if left_available:

                left_crop, left_box = self.crop_hand(
                    image,
                    kp2d[LEFT_WRIST],
                    kp2d[LEFT_ELBOW]
                )

                left_gesture, left_confidence = self.classify_hand(left_crop)

                self.add_hand_marker(
                    hand_markers,
                    hand_marker_id,
                    msg.header.stamp,
                    kp3d[LEFT_WRIST],
                    left_gesture,
                    body_index,
                    'left'
                )

                hand_marker_id += 2

            # ====================================================
            # RIGHT HAND
            # ====================================================

            if right_available:

                right_crop, right_box = self.crop_hand(
                    image,
                    kp2d[RIGHT_WRIST],
                    kp2d[RIGHT_ELBOW]
                )

                right_gesture, right_confidence = self.classify_hand(right_crop)

                self.add_hand_marker(
                    hand_markers,
                    hand_marker_id,
                    msg.header.stamp,
                    kp3d[RIGHT_WRIST],
                    right_gesture,
                    body_index,
                    'right'
                )

                hand_marker_id += 2

            # ====================================================
            # STRUCTURED OUTPUT
            # ====================================================

            center = self.calculate_body_center(
                kp3d
            )

            if center is not None:

                state = HandGestureState()

                state.skeleton_index = body_index
                state.position = center

                state.left_gesture = left_gesture
                state.left_gesture_confidence = left_confidence

                state.right_gesture = right_gesture
                state.right_gesture_confidence = right_confidence

                states.skeletons.append(state)

            # ====================================================
            # 2D SKELETON
            # ====================================================

            if image is not None:

                for start, end in BODY_18_BONES:

                    p1 = kp2d[start]
                    p2 = kp2d[end]

                    if (
                        self.valid_2d(p1) and
                        self.valid_2d(p2)
                    ):

                        cv2.line(
                            image,
                            (int(p1[0]), int(p1[1])),
                            (int(p2[0]), int(p2[1])),
                            (0, 255, 0),
                            3
                        )

                for kp in kp2d:

                    if self.valid_2d(kp):

                        cv2.circle(
                            image,
                            (
                                int(kp[0]),
                                int(kp[1])
                            ),
                            6,
                            (0, 0, 255),
                            -1
                        )

                # Left hand crop region
                if left_box is not None:

                    x1, y1, x2, y2 = left_box

                    cv2.rectangle(
                        image,
                        (x1, y1),
                        (x2, y2),
                        (0, 255, 0),
                        2
                    )

                    cv2.putText(
                        image,
                        left_gesture,
                        (x1, max(30, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 0),
                        2
                    )

                # Right hand crop region
                if right_box is not None:

                    x1, y1, x2, y2 = right_box

                    cv2.rectangle(
                        image,
                        (x1, y1),
                        (x2, y2),
                        (255, 150, 0),
                        2
                    )

                    cv2.putText(
                        image,
                        right_gesture,
                        (x1, max(30, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (255, 150, 0),
                        2
                    )

        # ========================================================
        # PUBLISH
        # ========================================================

        self.skeleton_marker_pub.publish(
            skeleton_markers
        )

        self.hand_marker_pub.publish(
            hand_markers
        )

        self.state_pub.publish(
            states
        )

        if image is not None:

            image_msg = self.bridge.cv2_to_imgmsg(
                image,
                encoding='bgr8'
            )

            image_msg.header = self.latest_image_header

            self.image_pub.publish(
                image_msg
            )


def main(args=None):

    rclpy.init(args=args)

    node = ZedSkeletonVisualizer()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    node.gesture_recognizer.close()

    node.destroy_node()

    rclpy.shutdown()


if __name__ == '__main__':
    main()