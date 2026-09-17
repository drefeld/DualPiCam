#!/usr/bin/env python3
"""
Core application controller for DualPiCam.

CameraManager is the central business-logic class. It connects every UI control
(sliders, dropdowns, buttons) to the underlying Picamera2 API and manages:
  - Camera control synchronization between linked cameras A and B
  - Video recording via RecordingWorker threads
  - Time-lapse photography with configurable intervals and auto-stop
  - Single and dual-camera snapshots
  - Metadata log files written alongside every recording
  - Profile save/load (JSON files in the Profiles/ directory)

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

from PyQt5.QtCore import QObject, QTimer, QTime, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QMessageBox, QFileDialog
from camera_controls import CameraControls, calculate_max_exposure, AF_MODE_MANUAL
from camera_config import apply_camera_config, parse_resolution_text
from picamera2.encoders import Quality
from utils import APP_VERSION, get_profile_name_dialog
from frame_processor import RecordingWorker
from datetime import datetime
import os
import json
import glob
import threading

class CameraManager(QObject):
    """Manages camera operations and connects UI to camera functionality"""

    # Capture threads report back through these signals rather than touching Qt
    # widgets directly, which is not allowed off the GUI thread. QTimer does not
    # work there either, since a plain python thread has no Qt event loop.
    # Payload: camera, file path, error message ('' when the capture succeeded).
    snapshot_finished = pyqtSignal(str, str, str)
    timelapse_capture_finished = pyqtSignal(str, str, str)

    def __init__(self, picam2a, picam2b, gui):
        super().__init__()
        self.picam2a = picam2a
        self.picam2b = picam2b
        self.gui = gui

        # Initialize camera controls
        self.controls_a = CameraControls(picam2a)
        self.controls_b = CameraControls(picam2b)

        # Initialize camera state
        self.current_fps_a = 10
        self.current_fps_b = 10
        self.current_resolution_a = (2560, 1440)
        self.current_resolution_b = (2560, 1440)
        self.current_buffer_size_a = 16
        self.current_buffer_size_b = 16
        self.current_quality_a = Quality.MEDIUM
        self.current_quality_b = Quality.MEDIUM
        self.record_audio_a = False
        self.record_audio_b = False
        self.current_sharpness_a = 1.0
        self.current_sharpness_b = 1.0

        # Initialize recording state
        self.recording_a = False
        self.recording_b = False

        # Default save locations (will be set by MainWindow)
        self.save_location_a = ""
        self.custom_filename_a = ""
        self.save_location_b = ""
        self.custom_filename_b = ""

        self.recording_timer_a = None
        self.recording_timer_b = None
        self.recording_start_time_a = None
        self.recording_start_time_b = None
        self.recording_duration_minutes_a = 0  # 0 = infinite
        self.recording_duration_minutes_b = 0  # 0 = infinite

        # Frame counters
        self.frame_count_a = 0
        self.frame_count_b = 0

        # Current recording filenames (for logs and snaps)
        self.current_recording_file_a = None
        self.current_recording_file_b = None

        # Flag to prevent multiple concurrent snapshot operations
        self.snapshot_in_progress_a = False
        self.snapshot_in_progress_b = False

        # Tracking log files
        self.current_log_file_a = None
        self.current_log_file_b = None

        # Time-lapse state
        self.timelapse_active_a = False
        self.timelapse_active_b = False
        self.timelapse_timer_a = None
        self.timelapse_timer_b = None
        # Connect status timers once here to avoid signal accumulation across sessions
        self.timelapse_status_timer_a = QTimer()
        self.timelapse_status_timer_a.timeout.connect(lambda: self._update_timelapse_status('A'))
        self.timelapse_status_timer_b = QTimer()
        self.timelapse_status_timer_b.timeout.connect(lambda: self._update_timelapse_status('B'))
        self.timelapse_counter_a = 0
        self.timelapse_counter_b = 0
        self.timelapse_start_time_a = None
        self.timelapse_start_time_b = None
        self.timelapse_directory_a = None
        self.timelapse_directory_b = None
        self.timelapse_prefix_a = None
        self.timelapse_prefix_b = None
        self.timelapse_interval_a = 0
        self.timelapse_interval_b = 0
        self.timelapse_duration_minutes_a = 0
        self.timelapse_duration_minutes_b = 0
        # Set while a capture thread is running, so a tick that arrives before
        # the previous capture finished is skipped rather than queued
        self.timelapse_capturing_a = False
        self.timelapse_capturing_b = False
        # Flag to prevent duplicate completion messages when cameras are linked
        self.timelapse_completion_message_shown = False

        # Create profile directory if it doesn't exist
        self.profiles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Profiles")
        os.makedirs(self.profiles_dir, exist_ok=True)

        # Recording workers (initialized as None)
        self.recording_worker_a = None
        self.recording_worker_b = None

        # Set while the window is closing, so teardown does not pop up dialogs
        # or schedule work on an event loop that is about to end.
        self.shutting_down = False

        # Collected so a linked (dual-camera) snapshot reports once, not twice
        self._snapshot_results = []

        # Capture threads marshal their results back to the GUI thread here
        self.snapshot_finished.connect(self._on_snapshot_finished)
        self.timelapse_capture_finished.connect(self._on_timelapse_capture_finished)

    # ------------------------------------------------------------------ #
    # Camera-specific accessor helpers                                     #
    # ------------------------------------------------------------------ #

    def _picam(self, camera):
        """Return the Picamera2 instance for the given camera."""
        return self.picam2a if camera == 'A' else self.picam2b

    def _controls(self, camera):
        """Return the CameraControls instance for the given camera."""
        return self.controls_a if camera == 'A' else self.controls_b

    def _panel(self, camera):
        """Return the CameraControlsPanel for the given camera."""
        return self.gui.control_panel_a if camera == 'A' else self.gui.control_panel_b

    def _rec_controls(self, camera):
        """Return the RecordingControlsPanel for the given camera."""
        return self.gui.recording_controls_a if camera == 'A' else self.gui.recording_controls_b

    def _rec_indicator(self, camera):
        """Return the recording indicator widget for the given camera."""
        return self.gui.recording_indicator_a if camera == 'A' else self.gui.recording_indicator_b

    def _tl_indicator(self, camera):
        """Return the time-lapse indicator widget for the given camera."""
        return self.gui.timelapse_indicator_a if camera == 'A' else self.gui.timelapse_indicator_b

    def _rec_duration_label(self, camera):
        """Return the recording duration label for the given camera."""
        return self.gui.recording_duration_label_a if camera == 'A' else self.gui.recording_duration_label_b

    def _tl_status_label(self, camera):
        """Return the time-lapse status label for the given camera."""
        return self.gui.timelapse_status_label_a if camera == 'A' else self.gui.timelapse_status_label_b

    def _tl_status_timer(self, camera):
        """Return the time-lapse status QTimer for the given camera."""
        return self.timelapse_status_timer_a if camera == 'A' else self.timelapse_status_timer_b

    def _save_location_box(self, camera):
        """Return the save-location text box for the given camera."""
        return self.gui.save_location_input_a if camera == 'A' else self.gui.save_location_input_b

    def _fps_label(self, camera):
        """Return the FPS display label for the given camera."""
        return self.gui.fps_label_a if camera == 'A' else self.gui.fps_label_b

    def _cam_checkbox(self, camera):
        """Return the camera-enabled checkbox for the given camera."""
        return self.gui.camera_a_checkbox if camera == 'A' else self.gui.camera_b_checkbox

    def _other(self, camera):
        """Return the identifier of the other camera."""
        return 'B' if camera == 'A' else 'A'

    def _cam_attr(self, camera, name):
        """Get a camera-specific attribute (e.g. 'recording' → self.recording_a)."""
        return getattr(self, f"{name}_{'a' if camera == 'A' else 'b'}")

    def _set_cam_attr(self, camera, name, value):
        """Set a camera-specific attribute (e.g. 'recording' → self.recording_a)."""
        setattr(self, f"{name}_{'a' if camera == 'A' else 'b'}", value)

    def _frame_callback(self, camera):
        """Return the frame-counter callback for the given camera."""
        return self.on_frame_a if camera == 'A' else self.on_frame_b

    def _restart_preview(self, camera):
        """Return the restart-preview callable for the given camera."""
        return self.gui.restart_preview_a if camera == 'A' else self.gui.restart_preview_b

    def _busy(self, camera):
        """True while the camera is recording or running a time-lapse."""
        return self._cam_attr(camera, 'recording') or self._cam_attr(camera, 'timelapse_active')

    @staticmethod
    def _set_without_signals(widget, setter, value):
        """Mirror a value onto a widget without re-entering its own handler.

        The linked-camera paths update the partner widget and then call the
        handler explicitly with sync=False. Blocking the widget's own signal
        keeps it from running the handler a second time; for resolution and
        buffer size that would mean two full camera stop/configure/start
        cycles instead of one.
        """
        widget.blockSignals(True)
        try:
            setter(value)
        finally:
            widget.blockSignals(False)

    def _apply_exposure_and_gain(self, camera):
        """Send exposure and gain to the camera as one pair.

        They share the single AeEnable flag, so they can only be applied
        together (see CameraControls.set_exposure_and_gain). Leaving one
        slider at Auto (0) while the other is manual is a supported
        combination: only when both read 0 does the camera return to full
        auto.

        set_controls() queues its change for the next camera request, and a
        request already in flight can swallow it, leaving the camera on its
        previous settings. Since there is no completion signal to check, every
        write is re-sent once after a short delay, provided the sliders still
        ask for the same thing by then.
        """
        panel = self._panel(camera)
        exposure = panel.exposure_slider.value()
        gain = panel.analog_gain_slider.value() / 100.0
        self._controls(camera).set_exposure_and_gain(exposure, gain)

        QTimer.singleShot(400, lambda: self._reapply_exposure_and_gain(camera, exposure, gain))

    def _reapply_exposure_and_gain(self, camera, expected_exposure, expected_gain):
        """Re-send the exposure/gain pair in case the first write raced a
        queued frame and was silently dropped.

        Only fires if the sliders still read exactly what was just sent -
        the user may have changed one of them again in the meantime, and
        re-applying a stale value would undo that.
        """
        panel = self._panel(camera)
        exposure = panel.exposure_slider.value()
        gain = panel.analog_gain_slider.value() / 100.0
        if exposure == expected_exposure and gain == expected_gain:
            self._controls(camera).set_exposure_and_gain(exposure, gain)

    # ------------------------------------------------------------------ #
    # Camera control handlers                                              #
    # ------------------------------------------------------------------ #

    def on_fps_slider_change(self, camera, value, sync=True):
        """Handle FPS slider change for specific camera and update exposure slider range"""
        self._controls(camera).set_fps(value)
        self._panel(camera).update_fps_label(value)
        self._set_cam_attr(camera, 'current_fps', value)

        max_exposure = calculate_max_exposure(value)
        adjusted_value = self._panel(camera).update_exposure_slider_range(max_exposure)
        if adjusted_value is not None:
            self.on_exposure_slider_change(camera, adjusted_value, False)

        if sync and self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                slider = self._panel(other).fps_slider
                self._set_without_signals(slider, slider.setValue, value)
                self.on_fps_slider_change(other, value, sync=False)
            finally:
                self.gui.updating_linked_control = False

    def on_exposure_slider_change(self, camera, value, sync=True):
        """Handle exposure slider change for specific camera"""
        self._apply_exposure_and_gain(camera)
        self._panel(camera).update_exposure_label(value)

        if sync and self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                slider = self._panel(other).exposure_slider
                self._set_without_signals(slider, slider.setValue, value)
                self.on_exposure_slider_change(other, value, sync=False)
            finally:
                self.gui.updating_linked_control = False

    def on_analog_gain_slider_change(self, camera, value, sync=True):
        """Handle gain slider change for specific camera

        value is the gain itself (0.0-16.0), not the raw slider position.
        """
        self._apply_exposure_and_gain(camera)
        self._panel(camera).update_analog_gain_label(value)

        if sync and self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                # value may already be the float gain or the raw slider int; normalise to slider int
                slider_value = int(value * 100) if isinstance(value, float) else value
                slider = self._panel(other).analog_gain_slider
                self._set_without_signals(slider, slider.setValue, slider_value)
                self.on_analog_gain_slider_change(other, value, sync=False)
            finally:
                self.gui.updating_linked_control = False

    def on_contrast_slider_change(self, camera, value, sync=True):
        """Handle contrast slider change for specific camera"""
        self._controls(camera).set_contrast(value / 100.0)
        self._panel(camera).update_contrast_label(value)

        if sync and self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                slider = self._panel(other).contrast_slider
                self._set_without_signals(slider, slider.setValue, value)
                self.on_contrast_slider_change(other, value, sync=False)
            finally:
                self.gui.updating_linked_control = False

    def on_sharpness_slider_change(self, camera, value, sync=True):
        """Handle sharpness slider change for specific camera"""
        sharpness_value = value / 100.0
        self._controls(camera).set_sharpness(sharpness_value)
        self._panel(camera).update_sharpness_label(value)
        self._set_cam_attr(camera, 'current_sharpness', sharpness_value)

        if sync and self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                slider = self._panel(other).sharpness_slider
                self._set_without_signals(slider, slider.setValue, value)
                self.on_sharpness_slider_change(other, value, sync=False)
            finally:
                self.gui.updating_linked_control = False

    def on_lens_slider_change(self, camera, value):
        """Handle lens position slider change"""
        # set_lens_position already writes AfMode and LensPosition in one call
        self._controls(camera).set_lens_position(value)
        self._panel(camera).update_lens_position_label(value)

    def autofocus(self, camera):
        """Trigger autofocus for specific camera"""
        self.gui.autofocus_handler.start_autofocus(camera)

    def update_lens_position(self, camera, lens_position):
        """Update lens position display after autofocus completes"""
        # 0.0 dioptres means "focused at infinity" and is a perfectly valid
        # result, so only genuinely out-of-range values get clamped.
        lens_position = max(0.0, min(10.0, float(lens_position)))
        self._panel(camera).update_lens_position_slider(lens_position)
        self._panel(camera).update_lens_position_label(lens_position)
        # Keep manual mode so the slider stays authoritative after focusing
        self._picam(camera).set_controls({"AfMode": AF_MODE_MANUAL,
                                          "LensPosition": lens_position})

    def on_resolution_change(self, camera, index, sync=True):
        """Handle resolution change for specific camera"""
        panel = self._panel(camera)
        resolution = parse_resolution_text(panel.resolution_dropdown.currentText())

        # Changing resolution restarts the camera, which would tear the pipeline
        # down underneath a running encoder or time-lapse.
        if self._busy(camera):
            QMessageBox.warning(self.gui, "Camera Busy",
                                f"Camera {camera} is recording or running a time-lapse. "
                                "Stop it before changing the resolution.")
            self._restore_resolution_dropdown(camera)
            return

        self._set_cam_attr(camera, 'current_resolution', resolution)
        if self._cam_checkbox(camera).isChecked():
            apply_camera_config(self._picam(camera), resolution,
                                self._cam_attr(camera, 'current_fps'),
                                self._cam_attr(camera, 'current_buffer_size'),
                                post_callback=self._frame_callback(camera))

        if sync and self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                dropdown = self._panel(other).resolution_dropdown
                self._set_without_signals(dropdown, dropdown.setCurrentIndex, index)
                self.on_resolution_change(other, index, sync=False)
            finally:
                self.gui.updating_linked_control = False

    def _restore_resolution_dropdown(self, camera):
        """Put the resolution dropdown back on the resolution actually in use."""
        dropdown = self._panel(camera).resolution_dropdown
        current = tuple(self._cam_attr(camera, 'current_resolution'))
        for i in range(dropdown.count()):
            if parse_resolution_text(dropdown.itemText(i)) == current:
                self._set_without_signals(dropdown, dropdown.setCurrentIndex, i)
                return

    def on_buffer_size_changed(self, camera, text):
        """Handle buffer size change (fired when editing finishes, not per keystroke)"""
        try:
            buffer_size = int(text)
        except (TypeError, ValueError):
            return  # Ignore non-numeric input

        if buffer_size <= 0 or buffer_size == self._cam_attr(camera, 'current_buffer_size'):
            return

        if self._busy(camera):
            QMessageBox.warning(self.gui, "Camera Busy",
                                f"Camera {camera} is recording or running a time-lapse. "
                                "Stop it before changing the buffer size.")
            buffer_input = self._panel(camera).buffer_input
            self._set_without_signals(buffer_input, buffer_input.setText,
                                      str(self._cam_attr(camera, 'current_buffer_size')))
            return

        self._set_cam_attr(camera, 'current_buffer_size', buffer_size)
        if self._cam_checkbox(camera).isChecked():
            self.reconfigure_camera(camera)

        if self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                buffer_input = self._panel(other).buffer_input
                self._set_without_signals(buffer_input, buffer_input.setText, text)
                if not self._busy(other):
                    self._set_cam_attr(other, 'current_buffer_size', buffer_size)
                    if self._cam_checkbox(other).isChecked():
                        self.reconfigure_camera(other)
            finally:
                self.gui.updating_linked_control = False

    def reconfigure_camera(self, camera):
        """Reconfigure camera with current settings (no-op if already correct)"""
        apply_camera_config(self._picam(camera),
                            self._cam_attr(camera, 'current_resolution'),
                            self._cam_attr(camera, 'current_fps'),
                            self._cam_attr(camera, 'current_buffer_size'),
                            post_callback=self._frame_callback(camera))

    def on_quality_change(self, camera, index):
        """Handle encoding quality change"""
        quality_map = {
            0: Quality.VERY_LOW, 1: Quality.LOW, 2: Quality.MEDIUM,
            3: Quality.HIGH,     4: Quality.VERY_HIGH,
        }
        quality = quality_map.get(index, Quality.MEDIUM)
        self._set_cam_attr(camera, 'current_quality', quality)

        if self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                dropdown = self._panel(other).quality_dropdown
                self._set_without_signals(dropdown, dropdown.setCurrentIndex, index)
                self._set_cam_attr(other, 'current_quality', quality)
            finally:
                self.gui.updating_linked_control = False

    def on_audio_option_changed(self, camera, state):
        """Handle audio recording option change"""
        self._set_cam_attr(camera, 'record_audio', state == Qt.Checked)

        if self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            other = self._other(camera)
            self.gui.updating_linked_control = True
            try:
                checkbox = self._panel(other).audio_checkbox
                self._set_without_signals(checkbox, checkbox.setChecked,
                                          self._cam_attr(camera, 'record_audio'))
                self._set_cam_attr(other, 'record_audio', self._cam_attr(camera, 'record_audio'))
            finally:
                self.gui.updating_linked_control = False

    # ------------------------------------------------------------------ #
    # Recording                                                            #
    # ------------------------------------------------------------------ #

    def on_record_button_click(self, camera):
        """Handle record button click for specific camera"""
        # Latch the intent before acting. If start_recording bails out (camera
        # disabled, unwritable directory, encoder error) the flag afterwards is
        # still False, which read as "just stopped" would tear down the other
        # camera's running recording.
        was_recording = self._cam_attr(camera, 'recording')

        if was_recording:
            self.stop_recording(camera)
        else:
            self.start_recording(camera)

        if not self.gui.link_cameras_checkbox.isChecked():
            return

        other = self._other(camera)
        if was_recording:
            if self._cam_attr(other, 'recording'):
                self.stop_recording(other)
        elif self._cam_attr(camera, 'recording'):  # this camera really did start
            if not self._cam_attr(other, 'recording') and self._cam_checkbox(other).isChecked():
                self.start_recording(other)

    def write_log_file(self, camera, filename):
        """Write a log file with current camera settings"""
        try:
            log_filename = f"{os.path.splitext(filename)[0]}_Log.txt"
            panel = self._panel(camera)

            resolution_text = panel.resolution_dropdown.currentText()
            fps = self._cam_attr(camera, 'current_fps')
            exposure = panel.exposure_slider.value()
            gain = panel.analog_gain_slider.value() / 100.0
            contrast = panel.contrast_slider.value() / 100.0
            sharpness = self._cam_attr(camera, 'current_sharpness')
            buffer_size = self._cam_attr(camera, 'current_buffer_size')
            quality_text = panel.quality_dropdown.currentText()
            has_audio = self._cam_attr(camera, 'record_audio')
            lens_position = panel.lens_slider[2].value() / 100.0

            with open(log_filename, 'w') as log_file:
                log_file.write(f"Recording Log for Camera {camera}\n")
                log_file.write(f"Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                log_file.write(f"Recording File: {os.path.basename(filename)}\n\n")
                log_file.write("Camera Settings:\n")
                log_file.write(f"Resolution: {resolution_text}\n")
                log_file.write(f"FPS: {fps}\n")
                log_file.write(f"Exposure: {'Auto' if exposure == 0 else f'{exposure} µs'}\n")
                log_file.write(f"Analog Gain: {'Auto' if gain == 0 else f'{gain:.2f}'}\n")
                log_file.write(f"Contrast: {contrast:.2f}\n")
                log_file.write(f"Sharpness: {sharpness:.2f}\n")
                log_file.write(f"Buffer Size: {buffer_size}\n")
                log_file.write(f"Encoding Quality: {quality_text}\n")
                log_file.write(f"Audio: {'Enabled' if has_audio else 'Disabled'}\n")
                log_file.write(f"Lens Position: {lens_position:.2f}\n\n")
                log_file.write("Recording Information:\n")
                log_file.write(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_file.write("Duration: [Will be filled when recording stops]\n")

            return log_filename
        except Exception as e:
            print(f"Error creating log file: {e}")
            return None

    def update_log_file_duration(self, log_filename, duration_seconds):
        """Update the log file with recording duration and end time when recording stops"""
        if not log_filename or not os.path.exists(log_filename):
            return

        try:
            with open(log_filename, 'r') as file:
                lines = file.readlines()

            end_time = datetime.now()
            for i, line in enumerate(lines):
                if "Duration: [Will be filled when recording stops]" in line:
                    minutes = duration_seconds // 60
                    seconds = duration_seconds % 60
                    lines[i] = f"Duration: {minutes} minutes, {seconds} seconds\n"
                    lines.insert(i + 1, f"End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    break

            with open(log_filename, 'w') as file:
                file.writelines(lines)
        except Exception as e:
            print(f"Error updating log file duration: {e}")

    def generate_filename(self, camera):
        """Generate a filename for recording based on custom path"""
        panel = self._panel(camera)
        resolution_text = panel.resolution_dropdown.currentText()
        fps = self._cam_attr(camera, 'current_fps')
        custom_filename = self._cam_attr(camera, 'custom_filename')

        if custom_filename:
            save_dir = os.path.dirname(custom_filename)
            custom_prefix = os.path.splitext(os.path.basename(custom_filename))[0]
        else:
            save_dir = self._cam_attr(camera, 'save_location')
            custom_prefix = ""

        resolution_label = resolution_text.split(" ")[0]
        date_str = datetime.now().strftime("%y%m%d_%H%M%S")

        if custom_prefix:
            filename = f"{date_str}_cam{camera}_{resolution_label}_{fps}fps_{custom_prefix}.mp4"
        else:
            filename = f"{date_str}_cam{camera}_{resolution_label}_{fps}fps.mp4"

        return os.path.join(save_dir, filename)

    def start_recording(self, camera):
        """Start recording for the specified camera"""
        if not self._cam_checkbox(camera).isChecked():
            return

        if self._cam_attr(camera, 'timelapse_active'):
            QMessageBox.warning(self.gui, "Time-lapse Active",
                                f"Camera {camera} is running a time-lapse. "
                                "Stop it before recording.")
            return

        # Validate the auto-stop duration before starting anything, so a typo
        # cannot leave the camera recording with no auto-stop.
        duration_text = self._rec_controls(camera).duration_input.text().strip() or "0"
        try:
            duration_minutes = float(duration_text)
            if duration_minutes < 0:
                raise ValueError("negative duration")
        except ValueError:
            QMessageBox.warning(self.gui, "Invalid Input",
                                "Please enter a valid number of minutes for the recording "
                                "duration (0 = record until stopped).")
            return

        save_location = self._cam_attr(camera, 'save_location')
        custom_filename = self._cam_attr(camera, 'custom_filename')
        save_dir = os.path.dirname(custom_filename) if custom_filename else save_location

        try:
            os.makedirs(save_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self.gui, "Directory Creation Failed",
                                f"Could not create directory: {e}")
            return

        filename = self.generate_filename(camera)

        picam = self._picam(camera)
        apply_camera_config(picam,
                            self._cam_attr(camera, 'current_resolution'),
                            self._cam_attr(camera, 'current_fps'),
                            self._cam_attr(camera, 'current_buffer_size'),
                            post_callback=self._frame_callback(camera))
        if not picam.started:
            picam.start()

        panel = self._panel(camera)
        exposure = panel.exposure_slider.value()
        gain = panel.analog_gain_slider.value() / 100.0
        contrast = panel.contrast_slider.value() / 100.0
        sharpness = self._cam_attr(camera, 'current_sharpness')
        lens_position = panel.lens_slider[2].value() / 100.0

        worker = RecordingWorker(
            camera,
            picam,
            filename,
            self._cam_attr(camera, 'current_quality'),
            self._cam_attr(camera, 'current_buffer_size'),
            self._cam_attr(camera, 'record_audio'),
            self._cam_attr(camera, 'current_resolution'),
            self._cam_attr(camera, 'current_fps'),
            exposure, gain, contrast, sharpness, lens_position,
        )
        worker.recording_stopped.connect(self._handle_recording_stopped)
        worker.recording_error.connect(self._handle_recording_error)
        worker.recording_status.connect(self._update_recording_status)

        self._set_cam_attr(camera, 'recording_worker', worker)

        # Nothing is marked as recording until the encoder is actually running,
        # so a failed start cannot leave the UI (or the linked camera) confused.
        if not worker.start_recording():
            self._set_cam_attr(camera, 'recording_worker', None)
            return

        self._set_cam_attr(camera, 'recording', True)
        self._set_cam_attr(camera, 'recording_start_time', QTime.currentTime())
        self._set_cam_attr(camera, 'current_recording_file', filename)
        self._set_cam_attr(camera, 'current_log_file', self.write_log_file(camera, filename))
        self._set_cam_attr(camera, 'recording_duration_minutes', duration_minutes)

        self._rec_controls(camera).set_snapshot_enabled(False)
        self._panel(camera).set_settings_enabled(False)
        self._rec_controls(camera).update_record_button_text(True)
        self._rec_indicator(camera).setVisible(True)

        if duration_minutes > 0:
            duration_ms = int(duration_minutes * 60 * 1000) + 500
            # QTimer counts milliseconds in a 32-bit int (~24 days). Longer
            # sessions are stopped by the elapsed-time check in
            # _update_recording_status instead of wrapping around to a
            # near-instant timeout.
            if duration_ms < 2 ** 31 - 1:
                timer = QTimer()
                timer.timeout.connect(lambda: self.stop_recording(camera))
                timer.setSingleShot(True)
                timer.start(duration_ms)
                self._set_cam_attr(camera, 'recording_timer', timer)

    def _handle_recording_stopped(self, camera, duration_seconds):
        """Fill in the log file's duration when a recording ends"""
        log_file = self._cam_attr(camera, 'current_log_file')
        if log_file:
            self.update_log_file_duration(log_file, duration_seconds)
            self._set_cam_attr(camera, 'current_log_file', None)

    def _handle_recording_error(self, camera, error_msg):
        """Handle recording error signal from the recording worker"""
        print(f"Recording error on camera {camera}: {error_msg}")

        self._set_cam_attr(camera, 'recording', False)
        self._rec_controls(camera).update_record_button_text(False)
        self._rec_indicator(camera).setVisible(False)
        rec_timer = self._cam_attr(camera, 'recording_timer')
        if rec_timer:
            rec_timer.stop()
        self._rec_duration_label(camera).setText("00:00:00")
        self._rec_controls(camera).set_snapshot_enabled(True)
        self._panel(camera).set_settings_enabled(True)

        if not self.shutting_down:
            QMessageBox.critical(self.gui, "Recording Error",
                                 f"Error recording from camera {camera}: {error_msg}")

    def _update_recording_status(self, camera, elapsed_seconds):
        """Update recording duration display from worker thread signal"""
        elapsed_time = QTime(0, 0).addSecs(elapsed_seconds).toString("hh:mm:ss")
        duration_minutes = self._cam_attr(camera, 'recording_duration_minutes')

        # Backstop for the auto-stop timer (and the only stop for durations too
        # long for QTimer's 32-bit millisecond counter).
        if duration_minutes > 0 and elapsed_seconds >= int(duration_minutes * 60):
            self.stop_recording(camera)
            return

        if duration_minutes > 0:
            total_seconds = duration_minutes * 60
            remaining_seconds = max(0, int(total_seconds - elapsed_seconds))
            remaining_time = QTime(0, 0).addSecs(remaining_seconds).toString("hh:mm:ss")
            display_text = f"{elapsed_time} | {remaining_time}"
        else:
            display_text = f"{elapsed_time} | ∞"

        self._rec_duration_label(camera).setText(display_text)

    def stop_recording(self, camera):
        """Stop recording for specific camera"""
        if not self._cam_attr(camera, 'recording'):
            return

        worker = self._cam_attr(camera, 'recording_worker')
        if worker:
            # Synchronous: when this returns the encoder is stopped, the MP4 is
            # finalised and _handle_recording_stopped has written the duration
            # into the log file.
            worker.stop_recording()
            self._set_cam_attr(camera, 'recording_worker', None)

        self._rec_controls(camera).update_record_button_text(False)
        self._set_cam_attr(camera, 'recording', False)
        self._rec_controls(camera).set_snapshot_enabled(True)
        self._panel(camera).set_settings_enabled(True)
        self._rec_indicator(camera).setVisible(False)

        rec_timer = self._cam_attr(camera, 'recording_timer')
        if rec_timer:
            rec_timer.stop()

        self._set_cam_attr(camera, 'recording_start_time', None)
        self._set_cam_attr(camera, 'recording_duration_minutes', 0)
        self._rec_duration_label(camera).setText("00:00:00")

        # picamera2's stop_recording() stops the camera as well, so the preview
        # has to be restarted. The stop above is synchronous, so it can be
        # restarted immediately.
        if not self.shutting_down:
            self._restart_preview(camera)()

    def on_recording_duration_changed(self, camera, text):
        """Handle recording duration text change"""
        if self.gui.link_cameras_checkbox.isChecked() and not self.gui.updating_linked_control:
            self.gui.updating_linked_control = True
            try:
                self._rec_controls(self._other(camera)).duration_input.setText(text)
            finally:
                self.gui.updating_linked_control = False

    # ------------------------------------------------------------------ #
    # Snapshots                                                            #
    # ------------------------------------------------------------------ #

    def take_snapshot(self, camera):
        """Take a snapshot from specified camera"""
        if (self.gui.link_cameras_checkbox.isChecked()
                and not self.recording_a and not self.recording_b
                and self.gui.camera_a_checkbox.isChecked()
                and self.gui.camera_b_checkbox.isChecked()):
            self._take_linked_snapshots()
            return

        if self._cam_checkbox(camera).isChecked() and not self._cam_attr(camera, 'recording'):
            self._take_single_snapshot(camera)

    def _take_linked_snapshots(self):
        """Take snapshots from both cameras simultaneously"""
        if self.snapshot_in_progress_a or self.snapshot_in_progress_b:
            QMessageBox.information(self.gui, "Snapshot in Progress",
                                    "Please wait for the current snapshot to complete.")
            return

        self.snapshot_in_progress_a = True
        self.snapshot_in_progress_b = True

        try:
            snapshot_file_a = self._prepare_snapshot_filename('A')
            snapshot_file_b = self._prepare_snapshot_filename('B')
            threading.Thread(target=self._take_snapshot_thread, args=('A', snapshot_file_a), daemon=True).start()
            threading.Thread(target=self._take_snapshot_thread, args=('B', snapshot_file_b), daemon=True).start()
        except Exception as e:
            self.snapshot_in_progress_a = False
            self.snapshot_in_progress_b = False
            QMessageBox.critical(self.gui, "Snapshot Error", f"Failed to take snapshots: {e}")

    def _take_single_snapshot(self, camera):
        """Take a snapshot from a single camera"""
        if self._cam_attr(camera, 'snapshot_in_progress'):
            QMessageBox.information(self.gui, "Snapshot in Progress",
                                    "Please wait for the current snapshot to complete.")
            return

        self._set_cam_attr(camera, 'snapshot_in_progress', True)
        try:
            snapshot_file = self._prepare_snapshot_filename(camera)
            threading.Thread(target=self._take_snapshot_thread,
                             args=(camera, snapshot_file), daemon=True).start()
        except Exception as e:
            self._set_cam_attr(camera, 'snapshot_in_progress', False)
            QMessageBox.critical(self.gui, "Snapshot Error", f"Failed to take snapshot: {e}")

    def get_snapshot_directory(self, camera):
        """Get directory where snapshots should be saved"""
        current_file = self._cam_attr(camera, 'current_recording_file')
        if current_file:
            return os.path.dirname(current_file)
        custom_filename = self._cam_attr(camera, 'custom_filename')
        if custom_filename:
            return os.path.dirname(custom_filename)
        return self._cam_attr(camera, 'save_location')

    def _take_snapshot_thread(self, camera, snapshot_file):
        """Capture a snapshot (runs in a background thread)"""
        error = ""
        try:
            self._picam(camera).capture_file(snapshot_file)
        except Exception as e:
            error = str(e)

        # Hand the result to the GUI thread. Qt widgets must not be touched from
        # here, and QTimer.singleShot does not work on a plain python thread.
        self.snapshot_finished.emit(camera, snapshot_file, error)

    def _on_snapshot_finished(self, camera, snapshot_file, error):
        """Report a finished snapshot (GUI thread)"""
        self._set_cam_attr(camera, 'snapshot_in_progress', False)
        self._snapshot_results.append((camera, snapshot_file, error))

        # In linked mode wait for the partner so both cameras are reported in a
        # single dialog instead of two stacked ones.
        if self._cam_attr(self._other(camera), 'snapshot_in_progress'):
            return

        results = sorted(self._snapshot_results)
        self._snapshot_results = []
        if self.shutting_down or not results:
            return

        failures = [f"Camera {cam}: {err}" for cam, _, err in results if err]
        if failures:
            QMessageBox.critical(self.gui, "Snapshot Error",
                                 "Failed to take snapshot:\n" + "\n".join(failures))
            return

        saved = "\n".join(f"Camera {cam}: {path}" for cam, path, _ in results)
        QMessageBox.information(self.gui, "Snapshot Taken", f"Snapshot saved to:\n{saved}")

    def _prepare_snapshot_filename(self, camera):
        """Prepare filename for a snapshot"""
        save_dir = self.get_snapshot_directory(camera)
        os.makedirs(save_dir, exist_ok=True)

        panel = self._panel(camera)
        resolution_label = panel.resolution_dropdown.currentText().split(" ")[0]
        # Milliseconds included: two snapshots within the same second would
        # otherwise resolve to the same path and silently overwrite each other.
        date_str = datetime.now().strftime("%y%m%d_%H%M%S_%f")[:-3]
        custom_filename = self._cam_attr(camera, 'custom_filename')
        custom_prefix = os.path.splitext(os.path.basename(custom_filename))[0] if custom_filename else ""

        if custom_prefix:
            return os.path.join(save_dir, f"{date_str}_cam{camera}_{resolution_label}_{custom_prefix}.jpg")
        return os.path.join(save_dir, f"{date_str}_cam{camera}_{resolution_label}.jpg")

    # ------------------------------------------------------------------ #
    # Time-lapse                                                           #
    # ------------------------------------------------------------------ #

    def on_timelapse_button_click(self, camera):
        """Handle time-lapse button click"""
        if self._cam_attr(camera, 'timelapse_active'):
            self.stop_timelapse(camera)
            if self.gui.link_cameras_checkbox.isChecked():
                other = self._other(camera)
                if self._cam_attr(other, 'timelapse_active'):
                    self.stop_timelapse(other)
        else:
            self._show_timelapse_dialog(camera)

    def _show_timelapse_dialog(self, camera):
        """Show dialog to configure time-lapse parameters"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox, QSpinBox, QPushButton

        self.timelapse_completion_message_shown = False

        dialog = QDialog(self.gui)
        dialog.setWindowTitle(f"Time-lapse Settings - Camera {camera}")
        dialog.setModal(True)

        layout = QVBoxLayout()

        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("Capture interval (seconds):"))
        interval_spinbox = QDoubleSpinBox()
        interval_spinbox.setRange(0.10, 31536000.0)  # 0.1 s (up to 10 fps) to 1 year
        interval_spinbox.setValue(60.0)  # 1 minute by default
        interval_spinbox.setDecimals(2)
        interval_spinbox.setSingleStep(1.0)
        interval_layout.addWidget(interval_spinbox)
        layout.addLayout(interval_layout)

        interval_hint_label = QLabel()
        interval_hint_label.setStyleSheet("color: #a0a0a0;")
        layout.addWidget(interval_hint_label)

        duration_layout = QHBoxLayout()
        duration_layout.addWidget(QLabel("Duration (minutes, 0 = infinite):"))
        duration_spinbox = QSpinBox()
        duration_spinbox.setRange(0, 5256000)  # 0 (infinite) to 10 years
        duration_spinbox.setValue(0)
        duration_layout.addWidget(duration_spinbox)
        layout.addLayout(duration_layout)

        duration_hint_label = QLabel()
        duration_hint_label.setStyleSheet("color: #a0a0a0;")
        layout.addWidget(duration_hint_label)

        def update_interval_hint(seconds):
            interval_hint_label.setText(self._format_interval_hint(seconds))

        def update_duration_hint(minutes):
            duration_hint_label.setText(self._format_duration_hint(minutes))

        interval_spinbox.valueChanged.connect(update_interval_hint)
        duration_spinbox.valueChanged.connect(update_duration_hint)
        update_interval_hint(interval_spinbox.value())
        update_duration_hint(duration_spinbox.value())

        layout.addWidget(QLabel("Images will be saved in a dedicated time-lapse folder."))

        button_layout = QHBoxLayout()
        start_button = QPushButton("Start Time-lapse")
        cancel_button = QPushButton("Cancel")
        start_button.clicked.connect(lambda: self._start_timelapse_from_dialog(
            camera, interval_spinbox.value() / 60.0, duration_spinbox.value(), dialog))
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(start_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    @staticmethod
    def _format_interval_hint(total_seconds):
        """Human-readable breakdown of a capture interval, e.g.
        '1 hour, 40 minutes, 0 seconds' for 6000 seconds.

        Unlike the duration hint, the seconds component is always shown
        (never trimmed) since seconds is the unit the interval is actually
        entered in, down to 0.01 s precision.
        """
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours, minutes = int(days), int(hours), int(minutes)

        units = [("day", days), ("hour", hours), ("minute", minutes)]
        # Only drop leading zero units (a 45 s interval reads "45 seconds",
        # not "0 days, 0 hours, 0 minutes, 45 seconds").
        while units and units[0][1] == 0:
            units.pop(0)
        parts = [f"{value} {name}{'s' if value != 1 else ''}" for name, value in units]

        if abs(seconds - round(seconds)) < 1e-9:
            seconds_text = f"{int(round(seconds))}"
        else:
            seconds_text = f"{seconds:.2f}".rstrip('0').rstrip('.')
        parts.append(f"{seconds_text} second{'s' if seconds_text != '1' else ''}")

        return "= " + ", ".join(parts)

    @staticmethod
    def _format_duration_hint(duration_minutes):
        """Human-readable breakdown of a time-lapse duration, e.g.
        '2 days, 6 hours, 47 minutes'."""
        if duration_minutes <= 0:
            return "= runs until stopped"

        total_minutes = int(round(duration_minutes))
        days, remainder = divmod(total_minutes, 1440)
        hours, minutes = divmod(remainder, 60)
        units = [("day", days), ("hour", hours), ("minute", minutes)]

        # Drop trailing zero units (exactly 1 day -> "1 day", not "1 day, 0 hours, 0 minutes")
        while len(units) > 1 and units[-1][1] == 0:
            units.pop()
        # Drop leading zero units (45 minutes -> "45 minutes", not "0 days, 0 hours, 45 minutes")
        while len(units) > 1 and units[0][1] == 0:
            units.pop(0)

        parts = [f"{value} {name}{'s' if value != 1 else ''}" for name, value in units]
        return "= " + ", ".join(parts)

    def _start_timelapse_from_dialog(self, camera, interval, duration, dialog):
        """Start time-lapse with parameters from dialog"""
        dialog.accept()

        # Clear both cameras' session state up front, so the completion dialog
        # cannot report a stale directory and image count for a camera that
        # takes no part in this session.
        for cam in ('A', 'B'):
            self._set_cam_attr(cam, 'timelapse_directory', None)
            self._set_cam_attr(cam, 'timelapse_start_time', None)
            self._set_cam_attr(cam, 'timelapse_counter', 0)

        self.start_timelapse(camera, interval, duration)

        if self.gui.link_cameras_checkbox.isChecked():
            other = self._other(camera)
            if self._cam_checkbox(other).isChecked() and not self._cam_attr(other, 'timelapse_active'):
                self.start_timelapse(other, interval, duration)

    def start_timelapse(self, camera, interval_minutes, duration_minutes=0):
        """Start time-lapse capture for specified camera

        Args:
            camera: 'A' or 'B'
            interval_minutes: Minutes between captures (0.1 = 6 s, 60 = 1 h, 1440 = 1 day)
            duration_minutes: Total session duration in minutes (0 for infinite)
        """
        self.timelapse_completion_message_shown = False

        if not self._cam_checkbox(camera).isChecked():
            return
        if self._cam_attr(camera, 'recording'):
            QMessageBox.warning(self.gui, "Recording Active",
                                "Cannot start time-lapse while recording.")
            return
        if self._cam_attr(camera, 'timelapse_active'):
            QMessageBox.warning(self.gui, "Time-lapse Active",
                                f"Time-lapse is already running for Camera {camera}.")
            return

        if not self._prepare_timelapse_session(camera):
            return
        self._set_cam_attr(camera, 'timelapse_interval', interval_minutes)
        self._set_cam_attr(camera, 'timelapse_duration_minutes', duration_minutes)

        timer = QTimer()
        timer.timeout.connect(lambda: self._take_timelapse_image(camera))
        timer.start(int(interval_minutes * 60 * 1000))
        self._set_cam_attr(camera, 'timelapse_timer', timer)

        self._set_cam_attr(camera, 'timelapse_active', True)
        self._set_cam_attr(camera, 'timelapse_start_time', datetime.now())
        self._set_cam_attr(camera, 'timelapse_counter', 0)
        self._set_cam_attr(camera, 'timelapse_capturing', False)

        # The grid starts at t=0, so the first image is taken straight away and
        # the timer above supplies every later one.
        self._take_timelapse_image(camera)

        self._rec_controls(camera).update_timelapse_button_text(True)
        self._panel(camera).set_settings_enabled(False)
        self._tl_indicator(camera).setVisible(True)
        self._rec_duration_label(camera).hide()
        self._tl_status_timer(camera).start(1000)

    def stop_timelapse(self, camera):
        """Stop time-lapse capture for specified camera"""
        if not self._cam_attr(camera, 'timelapse_active'):
            return

        tl_timer = self._cam_attr(camera, 'timelapse_timer')
        if tl_timer:
            tl_timer.stop()
        self._tl_status_timer(camera).stop()
        self._set_cam_attr(camera, 'timelapse_active', False)
        # A capture still in flight drops its result, so clear the flag here
        self._set_cam_attr(camera, 'timelapse_capturing', False)

        total_images = self._cam_attr(camera, 'timelapse_counter')
        avg_interval_sec = None
        start_time = self._cam_attr(camera, 'timelapse_start_time')
        duration = None
        if start_time:
            duration = datetime.now() - start_time
            avg_interval_sec = self._average_interval(duration, total_images)
            self._create_timelapse_log(camera, duration, total_images)

        self._rec_controls(camera).update_timelapse_button_text(False)
        self._panel(camera).set_settings_enabled(True)
        self._tl_indicator(camera).setVisible(False)
        self._tl_status_label(camera).setText("")
        self._rec_duration_label(camera).show()

        if self.shutting_down:
            return

        other = self._other(camera)
        linked = self.gui.link_cameras_checkbox.isChecked()

        # If the other camera is still running in linked mode, let it emit the consolidated message
        if linked and self._cam_attr(other, 'timelapse_active'):
            return

        if self.timelapse_completion_message_shown:
            return

        self.timelapse_completion_message_shown = True
        dir_this = self._cam_attr(camera, 'timelapse_directory')

        # When cameras are linked check whether the other camera also participated
        if linked:
            other_dir = self._cam_attr(other, 'timelapse_directory')
            other_start = self._cam_attr(other, 'timelapse_start_time')
            if other_dir and other_start:
                other_images = self._cam_attr(other, 'timelapse_counter')
                other_avg_sec = self._average_interval(datetime.now() - other_start, other_images)
                QMessageBox.information(self.gui, "Time-lapse Complete",
                    f"Time-lapse completed for both cameras.\n\n"
                    f"Camera {other}:\n"
                    f"  Images saved to: {other_dir}\n"
                    f"  Total images: {other_images}\n"
                    f"  Average interval: {self._fmt_interval(other_avg_sec)}\n\n"
                    f"Camera {camera}:\n"
                    f"  Images saved to: {dir_this}\n"
                    f"  Total images: {total_images}\n"
                    f"  Average interval: {self._fmt_interval(avg_interval_sec)}")
                return

        QMessageBox.information(self.gui, "Time-lapse Complete",
            f"Time-lapse completed for Camera {camera}.\n"
            f"Images saved to: {dir_this}\n"
            f"Total images: {total_images}\n"
            f"Average interval: {self._fmt_interval(avg_interval_sec)}")

    def _prepare_timelapse_session(self, camera):
        """Prepare directory and naming for a time-lapse session.

        Returns False (after telling the user) when the session directory
        cannot be created, instead of letting an OSError escape from a Qt slot
        and take the process down.
        """
        custom_filename = self._cam_attr(camera, 'custom_filename')
        if custom_filename:
            base_dir = os.path.dirname(custom_filename)
            custom_prefix = os.path.splitext(os.path.basename(custom_filename))[0]
        else:
            base_dir = self._cam_attr(camera, 'save_location')
            custom_prefix = ""

        resolution_label = self._panel(camera).resolution_dropdown.currentText().split(" ")[0]
        date_str = datetime.now().strftime("%y%m%d_%H%M%S")

        if custom_prefix:
            session_name = f"{date_str}_cam{camera}_{resolution_label}_timelapse_{custom_prefix}"
            prefix = f"cam{camera}_{resolution_label}_timelapse_{custom_prefix}"
        else:
            session_name = f"{date_str}_cam{camera}_{resolution_label}_timelapse"
            prefix = f"cam{camera}_{resolution_label}_timelapse"

        directory = os.path.join(base_dir, session_name)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self.gui, "Time-lapse Error",
                                 f"Could not create the time-lapse directory:\n{directory}\n\n{e}")
            return False

        self._set_cam_attr(camera, 'timelapse_directory', directory)
        self._set_cam_attr(camera, 'timelapse_prefix', prefix)
        return True

    def _take_timelapse_image(self, camera):
        """Queue a single time-lapse image capture in a background thread.

        A tick that arrives while the previous capture is still running is
        skipped. Short intervals can outpace the camera - a full-resolution
        capture takes appreciably longer than the 0.1 s minimum interval - and
        queueing them anyway would pile up threads faster than they drain. The
        effective rate simply settles at what the camera can sustain.
        """
        if not self._cam_attr(camera, 'timelapse_active') or not self._cam_checkbox(camera).isChecked():
            return

        if self._cam_attr(camera, 'timelapse_capturing'):
            return

        self._set_cam_attr(camera, 'timelapse_capturing', True)
        timestamp = datetime.now().strftime("%y%m%d_%H%M%S_%f")[:-3]
        prefix = self._cam_attr(camera, 'timelapse_prefix')
        directory = self._cam_attr(camera, 'timelapse_directory')
        filepath = os.path.join(directory, f"{prefix}_{timestamp}.jpg")

        threading.Thread(
            target=self._capture_timelapse_image_thread,
            args=(camera, filepath),
            daemon=True,
        ).start()

    def _capture_timelapse_image_thread(self, camera, filepath):
        """Capture a single time-lapse image (runs in a background thread)"""
        error = ""
        try:
            if not self._cam_attr(camera, 'timelapse_active'):
                # Session ended between scheduling and running this capture.
                # stop_timelapse() clears the capturing flag, so just drop it.
                return
            self._picam(camera).capture_file(filepath)
        except Exception as e:
            error = str(e)

        # Report back on the GUI thread (see snapshot_finished)
        self.timelapse_capture_finished.emit(camera, filepath, error)

    def _on_timelapse_capture_finished(self, camera, filepath, error):
        """Account for a finished time-lapse capture (GUI thread)"""
        self._set_cam_attr(camera, 'timelapse_capturing', False)

        if error:
            print(f"Error capturing time-lapse image for Camera {camera}: {error}")
            self.stop_timelapse(camera)
            if not self.shutting_down:
                QMessageBox.critical(self.gui, "Time-lapse Error",
                                     f"Failed to capture a time-lapse image for Camera {camera}:\n"
                                     f"{error}\n\nTime-lapse stopped.")
            return

        # Count images that were actually written, not merely scheduled
        self._set_cam_attr(camera, 'timelapse_counter',
                           self._cam_attr(camera, 'timelapse_counter') + 1)

        # The regular capture grid (0, interval, 2*interval, ...) has now reached
        # the requested duration: stop here instead of scheduling another
        # capture. Deciding this from the actual capture count (rather than a
        # separate wall-clock poll) avoids taking a duplicate image right at
        # the boundary when the duration lines up exactly with an interval tick.
        duration_minutes = self._cam_attr(camera, 'timelapse_duration_minutes')
        interval_minutes = self._cam_attr(camera, 'timelapse_interval')
        expected_total = self._expected_timelapse_total(interval_minutes, duration_minutes)
        if expected_total is not None and self._cam_attr(camera, 'timelapse_counter') >= expected_total:
            self.stop_timelapse(camera)

    @staticmethod
    def _average_interval(duration, total_images):
        """Average seconds between images, or None when it is not meaningful."""
        if not duration or total_images < 2:
            return None
        return duration.total_seconds() / (total_images - 1)

    def _fmt_interval(self, seconds):
        """Format an interval in seconds to a human-readable string."""
        if seconds is None:
            return "n/a"
        if seconds < 120:
            return f"{seconds:.1f} sec"
        elif seconds < 7200:
            return f"{seconds / 60:.2f} min"
        else:
            return f"{seconds / 3600:.2f} hr"

    def _create_timelapse_log(self, camera, duration, total_images):
        """Create a summary log file for the time-lapse session"""
        try:
            directory = self._cam_attr(camera, 'timelapse_directory')
            prefix = self._cam_attr(camera, 'timelapse_prefix')
            log_path = os.path.join(directory, f"{prefix}_Log.txt")

            panel = self._panel(camera)
            resolution_text = panel.resolution_dropdown.currentText()
            exposure = panel.exposure_slider.value()
            gain = panel.analog_gain_slider.value() / 100.0
            contrast = panel.contrast_slider.value() / 100.0
            sharpness = self._cam_attr(camera, 'current_sharpness')
            lens_position = panel.lens_slider[2].value() / 100.0
            start_time = self._cam_attr(camera, 'timelapse_start_time')

            with open(log_path, 'w') as log_file:
                log_file.write(f"Time-lapse Log for Camera {camera}\n")
                log_file.write(f"Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                log_file.write("Camera Settings:\n")
                log_file.write(f"Resolution: {resolution_text}\n")
                log_file.write(f"Exposure: {'Auto' if exposure == 0 else f'{exposure} µs'}\n")
                log_file.write(f"Analog Gain: {'Auto' if gain == 0 else f'{gain:.2f}'}\n")
                log_file.write(f"Contrast: {contrast:.2f}\n")
                log_file.write(f"Sharpness: {sharpness:.2f}\n")
                log_file.write(f"Lens Position: {lens_position:.2f}\n\n")
                log_file.write("Time-lapse Information:\n")
                log_file.write(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
                log_file.write(f"End Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
                log_file.write(f"Duration: {self._fmt_interval(duration.total_seconds())}\n")
                log_file.write(f"Total Images: {total_images}\n")
                avg_sec = self._average_interval(duration, total_images)
                log_file.write(f"Average Interval: {self._fmt_interval(avg_sec)}\n")
        except Exception as e:
            print(f"Error creating time-lapse log: {e}")

    @staticmethod
    def _expected_timelapse_total(interval_minutes, duration_minutes):
        """Number of images the regular capture grid (0, interval, 2*interval,
        ...) produces within a duration-limited session, or None when the
        session has no fixed duration."""
        if duration_minutes <= 0 or interval_minutes <= 0:
            return None
        return int(duration_minutes / interval_minutes + 1e-6) + 1

    def _update_timelapse_status(self, camera):
        """Update time-lapse status display"""
        if not self._cam_attr(camera, 'timelapse_active'):
            return
        start_time = self._cam_attr(camera, 'timelapse_start_time')
        if not start_time:
            return

        elapsed = datetime.now() - start_time
        elapsed_seconds = int(elapsed.total_seconds())
        hours = elapsed_seconds // 3600
        minutes = (elapsed_seconds % 3600) // 60
        seconds = elapsed_seconds % 60
        elapsed_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        duration_minutes = self._cam_attr(camera, 'timelapse_duration_minutes')
        if duration_minutes > 0:
            total_seconds = duration_minutes * 60
            remaining_seconds = max(0, total_seconds - elapsed_seconds)
            r_h = remaining_seconds // 3600
            r_m = (remaining_seconds % 3600) // 60
            r_s = remaining_seconds % 60
            remaining_str = f"{r_h:02d}:{r_m:02d}:{r_s:02d}"
        else:
            remaining_str = "∞"

        taken_images = self._cam_attr(camera, 'timelapse_counter')
        interval_minutes = self._cam_attr(camera, 'timelapse_interval')
        if duration_minutes > 0:
            expected_total = self._expected_timelapse_total(interval_minutes, duration_minutes)
            expected_total = max(expected_total, taken_images)
            images_str = f"{taken_images} / {expected_total}"
        else:
            images_str = f"{taken_images} / ∞"

        self._tl_status_label(camera).setText(f"{elapsed_str} | {remaining_str}   {images_str} imgs")

    # ------------------------------------------------------------------ #
    # Camera control synchronisation                                       #
    # ------------------------------------------------------------------ #

    def on_link_cameras_changed(self, state):
        """Handle link cameras checkbox state change"""
        if state == Qt.Checked:
            self.sync_camera_controls('A', 'B')

    def sync_camera_controls(self, source, target):
        """Sync all controls from one camera to another.

        Each widget is updated with its signals blocked and the corresponding
        handler is then called once explicitly - otherwise every setting is
        applied twice, which for resolution and buffer size means two full
        camera restarts.
        """
        self.gui.updating_linked_control = True
        try:
            src = self._panel(source)
            tgt = self._panel(target)
            mirror = self._set_without_signals

            mirror(tgt.fps_slider, tgt.fps_slider.setValue, src.fps_slider.value())
            self.on_fps_slider_change(target, src.fps_slider.value(), sync=False)

            mirror(tgt.exposure_slider, tgt.exposure_slider.setValue, src.exposure_slider.value())
            self.on_exposure_slider_change(target, src.exposure_slider.value(), sync=False)

            mirror(tgt.analog_gain_slider, tgt.analog_gain_slider.setValue, src.analog_gain_slider.value())
            self.on_analog_gain_slider_change(target, src.analog_gain_slider.value() / 100.0, sync=False)

            mirror(tgt.contrast_slider, tgt.contrast_slider.setValue, src.contrast_slider.value())
            self.on_contrast_slider_change(target, src.contrast_slider.value(), sync=False)

            if src.sharpness_slider is not None and tgt.sharpness_slider is not None:
                mirror(tgt.sharpness_slider, tgt.sharpness_slider.setValue, src.sharpness_slider.value())
                self.on_sharpness_slider_change(target, src.sharpness_slider.value(), sync=False)

            index = src.resolution_dropdown.currentIndex()
            mirror(tgt.resolution_dropdown, tgt.resolution_dropdown.setCurrentIndex, index)
            self.on_resolution_change(target, index, sync=False)

            mirror(tgt.buffer_input, tgt.buffer_input.setText, src.buffer_input.text())
            if self._cam_attr(target, 'current_buffer_size') != self._cam_attr(source, 'current_buffer_size'):
                self._set_cam_attr(target, 'current_buffer_size', self._cam_attr(source, 'current_buffer_size'))
                if self._cam_checkbox(target).isChecked() and not self._busy(target):
                    self.reconfigure_camera(target)

            mirror(tgt.quality_dropdown, tgt.quality_dropdown.setCurrentIndex,
                   src.quality_dropdown.currentIndex())
            self._set_cam_attr(target, 'current_quality', self._cam_attr(source, 'current_quality'))

            mirror(tgt.audio_checkbox, tgt.audio_checkbox.setChecked, src.audio_checkbox.isChecked())
            self._set_cam_attr(target, 'record_audio', self._cam_attr(source, 'record_audio'))

            duration = self._rec_controls(source).duration_input
            target_duration = self._rec_controls(target).duration_input
            mirror(target_duration, target_duration.setText, duration.text())
        finally:
            self.gui.updating_linked_control = False

    # ------------------------------------------------------------------ #
    # Misc                                                                 #
    # ------------------------------------------------------------------ #

    def select_save_location(self, camera):
        """Select save location and filename for recordings"""
        default_dir = self._cam_attr(camera, 'save_location')
        custom_path, _ = QFileDialog.getSaveFileName(
            self.gui,
            f"Select Save Location and Filename for Camera {camera} Recordings",
            default_dir,
            "MP4 Files (*.mp4)",
        )
        if custom_path:
            self._set_cam_attr(camera, 'save_location', os.path.dirname(custom_path))
            self._set_cam_attr(camera, 'custom_filename', custom_path)
            self._save_location_box(camera).setText(custom_path)

    def on_save_location_edited(self, camera):
        """Handle manual edits to the save-location text box.

        A path that already exists as a directory (or is explicitly typed
        with a trailing slash) is treated as a save directory with
        auto-generated filenames, matching the default behaviour. Any other
        path is treated the same way the "Choose..." dialog treats it: the
        directory part becomes the save location and the whole path becomes
        the filename prefix for recordings and time-lapse sessions.
        """
        box = self._save_location_box(camera)
        text = box.text().strip()

        if not text:
            box.setText(self._cam_attr(camera, 'custom_filename') or self._cam_attr(camera, 'save_location'))
            return

        if text.endswith(("/", "\\")) or os.path.isdir(text):
            save_dir = text.rstrip("/\\") or text
            self._set_cam_attr(camera, 'save_location', save_dir)
            self._set_cam_attr(camera, 'custom_filename', "")
            box.setText(save_dir)
        else:
            self._set_cam_attr(camera, 'save_location', os.path.dirname(text))
            self._set_cam_attr(camera, 'custom_filename', text)
            box.setText(text)

    def open_save_location(self, camera):
        """Open the camera's current save directory in the OS file browser."""
        directory = self._cam_attr(camera, 'save_location')
        if not directory:
            return
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self.gui, "Open Folder Failed",
                                f"Could not create or access directory:\n{directory}\n\n{e}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def toggle_camera(self, camera, state):
        """Toggle camera on/off"""
        if state == Qt.Checked:
            self._picam(camera).start()
            if not self.gui.timer.isActive():
                # Same 1 s cadence as the initial timer, otherwise the FPS
                # readout silently switches to a 2 s update after a toggle.
                self.gui.timer.start(1000)
        else:
            if self._busy(camera):
                QMessageBox.warning(self.gui, "Camera Busy",
                                    f"Camera {camera} is recording or running a time-lapse. "
                                    "Stop it before switching the camera off.")
                checkbox = self._cam_checkbox(camera)
                self._set_without_signals(checkbox, checkbox.setChecked, True)
                return

            self._picam(camera).stop()
            self._fps_label(camera).setText(f"Camera {camera} FPS: Stopped")

        if not self.gui.camera_a_checkbox.isChecked() and not self.gui.camera_b_checkbox.isChecked():
            self.gui.timer.stop()

    def update_framerate(self):
        """Update FPS display for both cameras"""
        elapsed_time = self.gui.time.elapsed() / 1000.0
        if elapsed_time <= 0:
            return

        try:
            for camera in ('A', 'B'):
                if self._cam_checkbox(camera).isChecked():
                    fps = self._cam_attr(camera, 'frame_count') / elapsed_time
                    self._fps_label(camera).setText(f"Camera {camera} FPS: {fps:.2f}")
                else:
                    self._fps_label(camera).setText(f"Camera {camera} FPS: Stopped")
                self._set_cam_attr(camera, 'frame_count', 0)
            self.gui.time.restart()
        except Exception as e:
            print(f"Error updating FPS display: {e}")

    def on_frame_a(self, request):
        """Handle frame capture for camera A"""
        self.frame_count_a += 1

    def on_frame_b(self, request):
        """Handle frame capture for camera B"""
        self.frame_count_b += 1

    # ------------------------------------------------------------------ #
    # Profile management                                                   #
    # ------------------------------------------------------------------ #

    def load_profile(self, profile_name):
        """Load a saved camera profile"""
        if not profile_name:
            return

        profile_path = os.path.join(self.profiles_dir, f"{profile_name}.txt")
        if not os.path.exists(profile_path):
            QMessageBox.warning(self.gui, "Profile Not Found",
                                f"The profile '{profile_name}' was not found.")
            return

        if self._busy('A') or self._busy('B'):
            QMessageBox.warning(self.gui, "Camera Busy",
                                "Stop the recording or time-lapse before loading a profile.")
            return

        try:
            with open(profile_path, 'r') as file:
                profile_data = json.load(file)

            if "camera_a" not in profile_data or "camera_b" not in profile_data:
                raise ValueError("profile is missing its camera_a / camera_b sections")

            # Restore the link state first, then apply both panels with linking
            # suppressed: without the guard every value written to panel A is
            # mirrored onto B and then B's own values overwrite A again, so a
            # profile with different per-camera settings collapsed into B's.
            if "cameras_linked" in profile_data:
                # Signals blocked: both panels are applied explicitly below, so
                # the sync this would otherwise trigger is pure wasted work
                # (including a camera restart for the resolution).
                checkbox = self.gui.link_cameras_checkbox
                self._set_without_signals(checkbox, checkbox.setChecked,
                                          bool(profile_data["cameras_linked"]))

            self.gui.updating_linked_control = True
            try:
                self.gui.control_panel_a.apply_settings(profile_data["camera_a"])
                self.gui.control_panel_b.apply_settings(profile_data["camera_b"])

                # The buffer field applies on editingFinished, which setText
                # does not raise, so push both cameras' values explicitly.
                for cam in ('A', 'B'):
                    self.on_buffer_size_changed(cam, self._panel(cam).buffer_input.text())
            finally:
                self.gui.updating_linked_control = False

            if "camera_a_enabled" in profile_data:
                self.gui.camera_a_checkbox.setChecked(bool(profile_data["camera_a_enabled"]))
            if "camera_b_enabled" in profile_data:
                self.gui.camera_b_checkbox.setChecked(bool(profile_data["camera_b_enabled"]))

            QMessageBox.information(self.gui, "Profile Loaded",
                                    f"Profile '{profile_name}' loaded successfully.")
        except Exception as e:
            QMessageBox.critical(self.gui, "Error Loading Profile",
                                 f"Failed to load profile '{profile_name}': {e}")

    def save_profile(self):
        """Save current camera settings as a profile"""
        existing_profiles = [os.path.splitext(os.path.basename(f))[0]
                             for f in glob.glob(os.path.join(self.profiles_dir, "*.txt"))]

        profile_name = get_profile_name_dialog(self.gui, existing_profiles)
        if not profile_name:
            return

        try:
            profile_data = {
                "camera_a": self.gui.control_panel_a.get_current_settings(),
                "camera_b": self.gui.control_panel_b.get_current_settings(),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "app_version": APP_VERSION,
                "camera_a_enabled": self.gui.camera_a_checkbox.isChecked(),
                "camera_b_enabled": self.gui.camera_b_checkbox.isChecked(),
                "cameras_linked": self.gui.link_cameras_checkbox.isChecked(),
            }

            profile_path = os.path.join(self.profiles_dir, f"{profile_name}.txt")
            with open(profile_path, 'w') as file:
                json.dump(profile_data, file, indent=4)

            QMessageBox.information(self.gui, "Profile Saved",
                                    f"Profile '{profile_name}' saved successfully.")
            self.refresh_profiles()
        except Exception as e:
            QMessageBox.critical(self.gui, "Error Saving Profile",
                                 f"Failed to save profile: {e}")

    def refresh_profiles(self):
        """Refresh the profiles dropdown menu"""
        profile_files = glob.glob(os.path.join(self.profiles_dir, "*.txt"))
        profile_names = sorted(os.path.splitext(os.path.basename(f))[0] for f in profile_files)

        if hasattr(self.gui, 'profile_dropdown'):
            self.gui.profile_dropdown.clear()
            self.gui.profile_dropdown.addItems(profile_names)

    # ------------------------------------------------------------------ #
    # Shutdown                                                             #
    # ------------------------------------------------------------------ #

    def shutdown(self):
        """Finish everything cleanly before the window closes.

        Recordings and time-lapse sessions are stopped through their normal
        paths, so the MP4 container is closed and both log files are completed.
        Skipping that would leave an unplayable file and a log still reading
        "Duration: [Will be filled when recording stops]".
        """
        self.shutting_down = True

        for camera in ('A', 'B'):
            try:
                if self._cam_attr(camera, 'timelapse_active'):
                    self.stop_timelapse(camera)
            except Exception as e:
                print(f"Error stopping time-lapse for camera {camera} during shutdown: {e}")

            try:
                if self._cam_attr(camera, 'recording'):
                    self.stop_recording(camera)
            except Exception as e:
                print(f"Error stopping recording for camera {camera} during shutdown: {e}")
