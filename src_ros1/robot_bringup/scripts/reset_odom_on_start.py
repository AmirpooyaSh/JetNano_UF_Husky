#!/usr/bin/env python3
"""Run once at launch: zero UM7 gyros, reset EKF odom to (0,0,0), clear costmaps.
The robot must be standing still on the start mark."""

import rospy
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_srvs.srv import Empty

try:
    from um7.srv import Reset as Um7Reset
except ImportError:
    Um7Reset = None


def zero_gyros():
    if Um7Reset is None:
        rospy.logerr("um7 package not found on this machine; gyro NOT zeroed.")
        return False
    try:
        rospy.wait_for_service("/imu/reset", timeout=10.0)
        reset = rospy.ServiceProxy("/imu/reset", Um7Reset)
        reset(zero_gyros=True, reset_ekf=False, set_mag_ref=False)
        rospy.loginfo("UM7 gyros zeroed.")
        return True
    except (rospy.ROSException, rospy.ServiceException) as error:
        rospy.logerr("Gyro zeroing failed: %s", error)
        return False


def reset_odom():
    pub = rospy.Publisher("/set_pose", PoseWithCovarianceStamped, queue_size=1, latch=True)
    msg = PoseWithCovarianceStamped()
    msg.header.stamp = rospy.Time.now()
    msg.header.frame_id = "odom"
    msg.pose.pose.orientation.w = 1.0          # x=0, y=0, yaw=0
    for i in (0, 7, 14, 21, 28, 35):           # small diagonal covariance
        msg.pose.covariance[i] = 1e-6
    rospy.sleep(1.0)                            # let the EKF connect
    pub.publish(msg)
    rospy.sleep(1.0)
    rospy.loginfo("Odom reset to (0, 0, 0).")


def clear_costmaps():
    try:
        rospy.wait_for_service("/move_base/clear_costmaps", timeout=20.0)
        rospy.ServiceProxy("/move_base/clear_costmaps", Empty)()
        rospy.loginfo("move_base costmaps cleared.")
    except (rospy.ROSException, rospy.ServiceException) as error:
        rospy.logwarn("Could not clear costmaps: %s", error)


if __name__ == "__main__":
    rospy.init_node("reset_odom_on_start")
    rospy.loginfo("Keep the robot still: zeroing gyro and resetting odom...")
    zero_gyros()
    rospy.sleep(3.0)    # let the gyro settle after zeroing
    reset_odom()
    clear_costmaps()
    rospy.loginfo("Start pose ready: odom = (0, 0, 0).")