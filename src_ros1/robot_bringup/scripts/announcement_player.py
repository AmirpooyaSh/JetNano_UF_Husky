#!/usr/bin/env python3
"""
Speaker side of the communication loop (runs on the Jetson).

Subscribes to /core/announcement (std_msgs/String) published by core_node and
plays the matching pre-generated WAV file through the robot's USB speaker:

    STOP_ACCEPTED        -> stop_accepted.wav
    STOP_REJECTED        -> stop_rejected.wav
    SLOW_DOWN_ACCEPTED   -> slow_down_accepted.wav
    SLOW_DOWN_REJECTED   -> slow_down_rejected.wav
    PROCEED_ACCEPTED     -> proceed_accepted.wav
    PROCEED_REJECTED     -> proceed_rejected.wav

A different announcement interrupts one that is still playing, so the speaker
always reports the robot's latest decision. The same announcement arriving
again while it is still playing is ignored instead of restarting it.
"""

import os
import subprocess
import threading

import rospy
from std_msgs.msg import String


VALID_CODES = {
    "STOP_ACCEPTED",
    "STOP_REJECTED",
    "SLOW_DOWN_ACCEPTED",
    "SLOW_DOWN_REJECTED",
    "PROCEED_ACCEPTED",
    "PROCEED_REJECTED",
}


class AnnouncementPlayer:
    def __init__(self):
        rospy.init_node("announcement_player")

        self.sound_dir = rospy.get_param("~sound_dir")
        self.audio_device = rospy.get_param("~audio_device", "default")
        self.topic = rospy.get_param("~topic", "/core/announcement")

        self.lock = threading.Lock()
        self.process = None
        self.current_code = None

        missing = [
            code for code in sorted(VALID_CODES)
            if not os.path.isfile(self.sound_path(code))
        ]
        if missing:
            rospy.logwarn(
                "Announcement files missing in %s: %s",
                self.sound_dir,
                ", ".join(code.lower() + ".wav" for code in missing),
            )

        rospy.Subscriber(self.topic, String, self.callback, queue_size=10)
        rospy.on_shutdown(self.stop_playback)
        rospy.loginfo(
            "Announcement player ready: topic=%s device=%s sounds=%s",
            self.topic,
            self.audio_device,
            self.sound_dir,
        )

    def sound_path(self, code):
        return os.path.join(self.sound_dir, code.lower() + ".wav")

    def stop_playback(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
            self.process = None

    def callback(self, msg):
        code = msg.data.strip().upper()
        if code not in VALID_CODES:
            rospy.logwarn("Unknown announcement code: %s", msg.data)
            return

        path = self.sound_path(code)
        if not os.path.isfile(path):
            rospy.logwarn("Announcement file not found: %s", path)
            return

        with self.lock:
            playing = self.process is not None and self.process.poll() is None

            # The same decision again while it is still being spoken is
            # ignored, so the sentence is not restarted ("co... co... co...").
            if playing and code == self.current_code:
                rospy.loginfo("Already playing %s; repeat ignored.", code)
                return

            # A different decision replaces the one still being spoken.
            if playing:
                self.process.terminate()
            self.current_code = code

            try:
                self.process = subprocess.Popen(
                    ["aplay", "-q", "-D", self.audio_device, path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except OSError as error:
                rospy.logerr("Could not start aplay: %s", error)
                self.process = None
                return

        rospy.loginfo("Playing announcement: %s", code)
        threading.Thread(
            target=self.report_errors, args=(self.process, code), daemon=True
        ).start()

    @staticmethod
    def report_errors(process, code):
        _, stderr = process.communicate()
        # A negative return code means it was interrupted on purpose.
        if process.returncode and process.returncode > 0:
            rospy.logerr(
                "aplay failed for %s: %s",
                code,
                stderr.decode(errors="replace").strip(),
            )


if __name__ == "__main__":
    AnnouncementPlayer()
    rospy.spin()