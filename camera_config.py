#!/usr/bin/env python3
"""
Camera initialization helpers for DualPiCam.

Provides functions for creating Picamera2 instances, applying video
configurations (resolution, FPS, buffer count), reconfiguring a running
camera only when the configuration actually changed, and parsing resolution
strings from the UI dropdown.

DualPiCam - A PyQt5-based camera application for research use
Copyright (C) 2025 David Brefeld

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
"""

from picamera2 import Picamera2

# Pixel format of the main stream, which is both previewed and encoded.
#
# YUV420 is the format the H.264 encoder actually wants. The Raspberry Pi 5 has
# no hardware H.264 block, so picamera2's H264Encoder resolves to
# LibavH264Encoder, which encodes to yuv420p in software. Feeding it XRGB8888
# makes libav colour-convert every frame of every camera (a full-frame
# BGRA -> YUV420p swscale pass), and each buffer costs 4 bytes/pixel instead of
# 1.5 - at 2560x1440 that is 14.1 MB per buffer instead of 5.3 MB.
#
# The Qt/OpenGL preview supports YUV420 natively, and JPEG snapshots get faster
# too: picamera2 encodes the YUV planes directly via simplejpeg instead of
# converting to RGB first.
#
# Set this back to "XRGB8888" if snapshots ever come out mangled - that would
# mean the installed picamera2 predates the direct YUV420 -> JPEG path (0.3.26).
MAIN_FORMAT = "YUV420"

# FPS used when a caller asks for a non-positive frame rate.
DEFAULT_FPS = 10


def create_camera_instances():
    """Create and initialize two camera instances.

    Raises RuntimeError with a readable message when fewer than two cameras
    are attached, so the app can show a dialog instead of a bare traceback.
    """
    detected = Picamera2.global_camera_info()
    if len(detected) < 2:
        names = ", ".join(cam.get("Model", "unknown") for cam in detected) or "none"
        raise RuntimeError(
            f"DualPiCam needs two cameras, but libcamera reports {len(detected)} "
            f"({names}).\n\n"
            "Check that both CSI cables are fully seated in the cam0 and cam1 ports, "
            "that camera_auto_detect=1 is set in /boot/firmware/config.txt, and that "
            "'libcamera-hello --list-cameras' lists Camera 0 and Camera 1."
        )

    picam2a = Picamera2(0)  # First camera
    picam2b = Picamera2(1)  # Second camera

    return picam2a, picam2b


def frame_duration_us(fps):
    """Return the frame duration in microseconds for a target frame rate."""
    if fps <= 0:
        fps = DEFAULT_FPS
    return int(1000000 / fps)


def build_video_configuration(picam, resolution, fps, buffer_count):
    """Build the video configuration used for both preview and recording.

    FrameDurationLimits is pinned to a single value so the sensor holds the
    requested rate instead of stretching frames in dim light. The preview then
    shows the same exposure behaviour that the recording will have.
    """
    frame_time = frame_duration_us(fps)
    return picam.create_video_configuration(
        main={"size": resolution, "format": MAIN_FORMAT},
        buffer_count=buffer_count,
        controls={"FrameDurationLimits": (frame_time, frame_time)},
    )


def configuration_matches(picam, resolution, fps, buffer_count):
    """True if the camera is already configured exactly this way.

    Used to skip a stop/configure/start cycle, which costs roughly a second and
    briefly blanks the preview. FPS is not part of the comparison because it is
    a control, not a stream property, and can be changed on a running camera.
    """
    config = picam.camera_configuration()
    if not config:
        return False
    main = config.get("main") or {}
    return (
        tuple(main.get("size") or ()) == tuple(resolution)
        and main.get("format") == MAIN_FORMAT
        and config.get("buffer_count") == buffer_count
    )


def configure_camera(picam, resolution, fps, buffer_count, post_callback=None):
    """Configure a stopped camera with the specified parameters."""
    picam.configure(build_video_configuration(picam, resolution, fps, buffer_count))

    # Set frame callback if provided
    if post_callback:
        picam.post_callback = post_callback

    return picam


def apply_camera_config(picam, resolution, fps, buffer_count, post_callback=None):
    """Bring a camera to the requested configuration, restarting only if needed.

    Returns True if the camera had to be stopped and reconfigured, False if it
    was already correct and only the frame rate control had to be refreshed.
    Must be called from the Qt main thread: with a QGlPicamera2 preview attached,
    picamera2 services stop() on the Qt event loop, so calling it from another
    thread blocks until the main thread gets around to it.
    """
    if picam.started and configuration_matches(picam, resolution, fps, buffer_count):
        frame_time = frame_duration_us(fps)
        picam.set_controls({"FrameDurationLimits": (frame_time, frame_time)})
        if post_callback:
            picam.post_callback = post_callback
        return False

    was_started = picam.started
    if was_started:
        picam.stop()
    configure_camera(picam, resolution, fps, buffer_count, post_callback)
    if was_started:
        picam.start()
    return True


def parse_resolution_text(resolution_text):
    """Parse resolution text like "1920x1080 (16:9)" to tuple (1920, 1080)"""
    # Extract resolution part from text (before any parentheses)
    resolution_part = resolution_text.split(" ")[0]

    # Parse width and height
    width, height = map(int, resolution_part.split("x"))

    return (width, height)
