#!/usr/bin/env python3
"""
Low-level camera control wrappers for DualPiCam.

CameraControls exposes set_* methods that map UI values to Picamera2 control
dictionaries. AutofocusHandler runs the autofocus cycle in a daemon thread
and emits a Qt signal with the resulting lens position when complete.

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

from PyQt5.QtCore import QObject, pyqtSignal
import threading

from camera_config import frame_duration_us

# libcamera AfMode enum values. Manual keeps whatever LensPosition we write,
# Auto runs a scan when triggered.
AF_MODE_MANUAL = 0
AF_MODE_AUTO = 1

# Lens position reported when autofocus could not produce a usable result.
DEFAULT_LENS_POSITION = 5.0


def calculate_max_exposure(fps):
    """Calculate maximum exposure time in microseconds based on FPS"""
    # Frame duration in microseconds (with 10% margin for processing)
    return int((1000000 / fps) * 0.9)

class CameraControls:
    """Class to manage camera controls"""

    def __init__(self, picam):
        self.picam = picam

    def set_fps(self, fps):
        """Set camera FPS.

        Both limits are pinned to the same value so the sensor holds the rate
        instead of stretching frames in dim light - the recording path does the
        same, so the preview shows what will actually be recorded.
        """
        frame_time = frame_duration_us(fps)
        self.picam.set_controls({"FrameDurationLimits": (frame_time, frame_time)})

    def set_exposure_and_gain(self, exposure, gain):
        """Set exposure and analogue gain together.

        AeEnable is a single flag covering both, so the two cannot be written
        independently and are always applied as a pair. libcamera has no
        half-automatic mode: only when both are 0 does auto-exposure run. As
        soon as either one is manual, AE is off for both, and the control left
        at 0 holds whatever value it had when AE was switched off.

        exposure: 0 for auto, otherwise exposure time in microseconds
        gain: 0 for auto, otherwise analogue gain value
        """
        if exposure == 0 and gain == 0:
            self.picam.set_controls({"AeEnable": True})
            return

        controls = {"AeEnable": False}
        if exposure > 0:
            controls["ExposureTime"] = exposure
        if gain > 0:
            controls["AnalogueGain"] = gain
        self.picam.set_controls(controls)

    def set_contrast(self, contrast):
        """Set camera contrast
        contrast: value between 0.0 and 16.0
        """
        self.picam.set_controls({"Contrast": contrast})

    def set_sharpness(self, sharpness):
        """Set camera sharpness
        sharpness: value between 0.0 and 16.0
        """
        self.picam.set_controls({"Sharpness": sharpness})

    def set_lens_position(self, position):
        """Set lens position (focus)
        position: value between 0 and 10
        """
        # Manual AF mode, otherwise the written position is overridden again
        self.picam.set_controls({
            "AfMode": AF_MODE_MANUAL,
            "LensPosition": position,
        })

    def get_lens_position(self):
        """Get current lens position"""
        if not self.picam.started:
            return DEFAULT_LENS_POSITION
        metadata = self.picam.capture_metadata()
        return metadata.get("LensPosition", DEFAULT_LENS_POSITION)

    def do_autofocus(self):
        """Run one autofocus cycle and return the resulting lens position.

        autofocus_cycle() sets AfMode to Auto, triggers a scan and blocks until
        libcamera reports Focused or Failed. It is dispatched through
        picamera2's job queue, so it is safe to call from a worker thread.
        """
        if not self.picam.started:
            return DEFAULT_LENS_POSITION

        self.picam.autofocus_cycle()
        lens_position = self.get_lens_position()

        # Hold the focus we just found: back to manual so the lens cannot drift
        # and the manual slider stays authoritative.
        self.picam.set_controls({
            "AfMode": AF_MODE_MANUAL,
            "LensPosition": lens_position,
        })

        return lens_position

class AutofocusHandler(QObject):
    """Handles autofocus operations in a separate thread"""

    # Signal to emit when autofocus is complete
    autofocus_done = pyqtSignal(str, float)

    def __init__(self, picam2a, picam2b):
        super().__init__()
        self.picam2a = picam2a
        self.picam2b = picam2b
        self.controls_a = CameraControls(picam2a)
        self.controls_b = CameraControls(picam2b)
        # One cycle per camera at a time; repeated button presses would
        # otherwise pile up threads that all fight over the lens.
        self._running = {'A': False, 'B': False}
        self._lock = threading.Lock()

    def start_autofocus(self, camera):
        """Start autofocus in a separate thread"""
        with self._lock:
            if self._running.get(camera):
                return
            self._running[camera] = True

        threading.Thread(
            target=self._run_autofocus,
            args=(camera,),
            daemon=True
        ).start()

    def _run_autofocus(self, camera):
        """Run autofocus and emit the result"""
        try:
            controls = self.controls_a if camera == 'A' else self.controls_b
            lens_position = controls.do_autofocus()

            # Emit the result
            self.autofocus_done.emit(camera, lens_position)
        except Exception as e:
            print(f"Autofocus error: {e}")
            # Emit a default value on error
            self.autofocus_done.emit(camera, DEFAULT_LENS_POSITION)
        finally:
            with self._lock:
                self._running[camera] = False
