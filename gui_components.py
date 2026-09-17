#!/usr/bin/env python3
"""
Reusable UI panels for DualPiCam.

CameraControlsPanel builds the per-camera grid of sliders and dropdowns
(FPS, exposure, gain, contrast, sharpness, resolution, buffer size, quality,
audio, lens position). RecordingControlsPanel provides the record / snapshot /
time-lapse button row. Both panels are instantiated once per camera in
gui_main.py.

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

from PyQt5.QtWidgets import (QLabel, QComboBox, QPushButton, QLineEdit,
                           QCheckBox, QGridLayout, QHBoxLayout, QVBoxLayout)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator
from utils import create_slider, create_labeled_slider
from camera_controls import calculate_max_exposure

# Encoding quality presets, in the order picamera2's Quality enum expects them.
QUALITY_OPTIONS = ["Very Low", "Low", "Medium", "High", "Very High"]

# Highest frame rate the FPS control accepts. Only reachable at low resolutions.
MAX_FPS = 120

class CameraControlsPanel:
    """Widget panel for camera controls"""
    def __init__(self, camera_id, initial_fps=10):
        self.camera_id = camera_id
        self.initial_fps = initial_fps
        self.controls_grid = QGridLayout()

        # Will be populated by setup_controls
        self.fps_slider = None
        self.fps_label = None
        self.exposure_slider = None
        self.exposure_label = None
        self.analog_gain_slider = None
        self.analog_gain_label = None
        self.contrast_slider = None
        self.contrast_label = None
        self.sharpness_slider = None
        self.sharpness_label = None
        self.resolution_dropdown = None
        self.buffer_input = None
        self.quality_dropdown = None
        self.audio_checkbox = None
        self.lens_slider = None
        
        # Flag to prevent feedback loops during manual edits
        self.updating_from_slider = False
    
    def setup_controls(self, fps_callback, exposure_callback, gain_callback,
                      contrast_callback, sharpness_callback, resolution_callback,
                      buffer_callback, quality_callback, audio_callback,
                      lens_callback, autofocus_callback, resolutions):
        """Build every control widget for this camera and wire up its callback.

        Returns the populated QGridLayout, which the main window drops into
        its own layout.
        """
        # FPS slider and editable label
        fps_layout = QHBoxLayout()
        fps_layout.setSpacing(5)
        fps_label_text = QLabel("FPS goal:")
        self.fps_label = QLineEdit(f"{self.initial_fps}")
        self.fps_label.setMaximumWidth(60)
        self.fps_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fps_label.editingFinished.connect(lambda: self._on_fps_edit(fps_callback))
        fps_layout.addWidget(fps_label_text)
        fps_layout.addWidget(self.fps_label)
        
        self.fps_slider = create_slider(1, MAX_FPS, self.initial_fps,
                                      lambda value: self._on_fps_slider_change(value, fps_callback))
        self.controls_grid.addLayout(fps_layout, 0, 0)
        self.controls_grid.addWidget(self.fps_slider, 0, 1)
        
        # Calculate initial maximum exposure based on default FPS
        initial_max_exposure = calculate_max_exposure(self.initial_fps)
        
        # Exposure slider and editable label
        exposure_layout = QHBoxLayout()
        exposure_layout.setSpacing(5)
        exposure_label_text = QLabel("Exposure:")
        self.exposure_label = QLineEdit("Auto")
        self.exposure_label.setMaximumWidth(120)
        self.exposure_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.exposure_label.editingFinished.connect(lambda: self._on_exposure_edit(exposure_callback))
        exposure_layout.addWidget(exposure_label_text)
        exposure_layout.addWidget(self.exposure_label)
        
        self.exposure_slider = create_slider(0, initial_max_exposure, 0, 
                                           lambda value: self._on_exposure_slider_change(value, exposure_callback))
        self.controls_grid.addLayout(exposure_layout, 1, 0)
        self.controls_grid.addWidget(self.exposure_slider, 1, 1)
        
        # Analog gain slider and editable label (with increased precision)
        gain_layout = QHBoxLayout()
        gain_layout.setSpacing(5)
        gain_label_text = QLabel("Gain:")
        self.analog_gain_label = QLineEdit("Auto")
        self.analog_gain_label.setMaximumWidth(80)
        self.analog_gain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.analog_gain_label.editingFinished.connect(lambda: self._on_gain_edit(gain_callback))
        gain_layout.addWidget(gain_label_text)
        gain_layout.addWidget(self.analog_gain_label)
        
        # Create gain slider with 100x more granularity (0-1600 for 0.00-16.00)
        self.analog_gain_slider = create_slider(0, 1600, 0, 
                                              lambda value: self._on_gain_slider_change(value, gain_callback))
        self.controls_grid.addLayout(gain_layout, 2, 0)
        self.controls_grid.addWidget(self.analog_gain_slider, 2, 1)
        
        # Contrast slider and editable label
        contrast_layout = QHBoxLayout()
        contrast_layout.setSpacing(5)
        contrast_label_text = QLabel("Contrast:")
        self.contrast_label = QLineEdit("1.00")
        self.contrast_label.setMaximumWidth(60)
        self.contrast_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.contrast_label.editingFinished.connect(lambda: self._on_contrast_edit(contrast_callback))
        contrast_layout.addWidget(contrast_label_text)
        contrast_layout.addWidget(self.contrast_label)

        # 100x granularity (0-1600 for 0.00-16.00): 0.1 steps were too coarse
        self.contrast_slider = create_slider(0, 1600, 100,
                                           lambda value: self._on_contrast_slider_change(value, contrast_callback))
        self.contrast_slider.setSingleStep(1)
        self.controls_grid.addLayout(contrast_layout, 3, 0)
        self.controls_grid.addWidget(self.contrast_slider, 3, 1)
        
        # Sharpness slider and editable label
        sharpness_layout = QHBoxLayout()
        sharpness_layout.setSpacing(5)
        sharpness_label_text = QLabel("Sharpness:")
        self.sharpness_label = QLineEdit("1.00")
        self.sharpness_label.setMaximumWidth(60)
        self.sharpness_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.sharpness_label.editingFinished.connect(lambda: self._on_sharpness_edit(sharpness_callback))
        sharpness_layout.addWidget(sharpness_label_text)
        sharpness_layout.addWidget(self.sharpness_label)

        # 100x granularity (0-1600 for 0.00-16.00), matching contrast and gain
        self.sharpness_slider = create_slider(0, 1600, 100,
                                           lambda value: self._on_sharpness_slider_change(value, sharpness_callback))
        self.sharpness_slider.setSingleStep(1)
        self.controls_grid.addLayout(sharpness_layout, 4, 0)
        self.controls_grid.addWidget(self.sharpness_slider, 4, 1)

        # Resolution dropdown
        resolution_label = QLabel("Resolution:")
        self.resolution_dropdown = QComboBox()
        self.resolution_dropdown.addItems(resolutions)
        self.resolution_dropdown.setCurrentText("2560x1440 (16:9)")  # Default
        self.resolution_dropdown.currentIndexChanged.connect(
            lambda index: resolution_callback(self.camera_id, index))
        self.controls_grid.addWidget(resolution_label, 5, 0)
        self.controls_grid.addWidget(self.resolution_dropdown, 5, 1)

        # Buffer size, quality and audio in a single row
        buffer_layout = QHBoxLayout()

        buffer_label = QLabel("Buffer Size:")
        self.buffer_input = QLineEdit("16")  # Default buffer size
        self.buffer_input.setValidator(QIntValidator(2, 64))
        # editingFinished rather than textChanged: a per-keystroke callback
        # would reconfigure and restart the camera for each partial number
        # typed, so entering "24" would first apply a 2-buffer configuration.
        self.buffer_input.editingFinished.connect(
            lambda: buffer_callback(self.camera_id, self.buffer_input.text()))

        quality_label = QLabel("Encoding Quality:")
        self.quality_dropdown = QComboBox()
        self.quality_dropdown.addItems(QUALITY_OPTIONS)
        self.quality_dropdown.setCurrentIndex(2)  # Default to MEDIUM
        self.quality_dropdown.currentIndexChanged.connect(
            lambda index: quality_callback(self.camera_id, index))
        
        audio_label = QLabel("Record Audio:")
        self.audio_checkbox = QCheckBox()
        self.audio_checkbox.setChecked(False)  # Default to no audio
        self.audio_checkbox.stateChanged.connect(
            lambda state: audio_callback(self.camera_id, state))
        
        buffer_layout.addWidget(buffer_label)
        buffer_layout.addWidget(self.buffer_input)
        buffer_layout.addWidget(quality_label)
        buffer_layout.addWidget(self.quality_dropdown)
        buffer_layout.addWidget(audio_label)
        buffer_layout.addWidget(self.audio_checkbox)
        
        self.controls_grid.addLayout(buffer_layout, 6, 0, 1, 2)  # Span 2 columns

        # Lens position slider with editable value box and autofocus button
        lens_layout = QVBoxLayout()
        self.lens_slider = create_labeled_slider(
            self.camera_id, "Lens Position Camera", 500,
            lens_callback,
            autofocus_callback,
        )
        lens_layout.addLayout(self.lens_slider[0])
        self.controls_grid.addLayout(lens_layout, 7, 0, 1, 2)  # Span 2 columns
        
        # Adjust column stretching for 1/3 - 2/3 split
        self.controls_grid.setColumnStretch(0, 1)  # Label column (1/3)
        self.controls_grid.setColumnStretch(1, 2)  # Control column (2/3)
        
        return self.controls_grid
    
    # Methods to handle slider changes
    
    def _on_fps_slider_change(self, value, callback):
        """Handle FPS slider changes and update label"""
        self.updating_from_slider = True
        self.fps_label.setText(str(value))
        self.updating_from_slider = False
        callback(self.camera_id, value)
    
    def _on_fps_edit(self, callback):
        """Handle manual edits to FPS value"""
        if self.updating_from_slider:
            return
            
        try:
            value = int(self.fps_label.text())
            value = max(1, min(MAX_FPS, value))
                
            self.fps_slider.setValue(value)
            # Note: Don't call callback here as the slider's valueChanged will trigger it
        except ValueError:
            # Restore the previous value if input is invalid
            self.update_fps_label(self.fps_slider.value())
    
    def _on_exposure_slider_change(self, value, callback):
        """Handle exposure slider changes and update label"""
        self.updating_from_slider = True
        if value == 0:
            self.exposure_label.setText("Auto")
        else:
            self.exposure_label.setText(f"{value} µs")
        self.updating_from_slider = False
        callback(self.camera_id, value)
    
    def _on_exposure_edit(self, callback):
        """Handle manual edits to exposure value"""
        if self.updating_from_slider:
            return
            
        try:
            text = self.exposure_label.text()
            if text.lower() == "auto":
                value = 0
            else:
                # Try to parse the value, removing any µs suffix
                value = int(text.replace("µs", "").strip())
            
            # Check if value is within range
            if value < 0:
                value = 0
            elif value > self.exposure_slider.maximum():
                value = self.exposure_slider.maximum()
                
            self.exposure_slider.setValue(value)
            # Note: Don't call callback here as the slider's valueChanged will trigger it
        except ValueError:
            # Restore the previous value if input is invalid
            self.update_exposure_label(self.exposure_slider.value())
    
    def _on_gain_slider_change(self, value, callback):
        """Handle gain slider changes and update label"""
        self.updating_from_slider = True
        gain_value = value / 100.0  # Convert from 0-1600 to 0-16.00
        if value == 0:
            self.analog_gain_label.setText("Auto")
        else:
            self.analog_gain_label.setText(f"{gain_value:.2f}")
        self.updating_from_slider = False
        callback(self.camera_id, gain_value)
    
    def _on_gain_edit(self, callback):
        """Handle manual edits to gain value"""
        if self.updating_from_slider:
            return
            
        try:
            text = self.analog_gain_label.text()
            if text.lower() == "auto":
                value = 0
            else:
                # Try to parse the decimal value
                gain_value = float(text)
                value = int(gain_value * 100)  # Convert from 0-16.00 to 0-1600
            
            # Check if value is within range
            if value < 0:
                value = 0
            elif value > self.analog_gain_slider.maximum():
                value = self.analog_gain_slider.maximum()
                
            self.analog_gain_slider.setValue(value)
            # Note: The slider's valueChanged will trigger the callback with proper scaling
        except ValueError:
            # Restore the previous value if input is invalid
            gain_value = self.analog_gain_slider.value() / 100.0
            self.update_analog_gain_label(gain_value)
    
    def _on_contrast_slider_change(self, value, callback):
        """Handle contrast slider changes and update label"""
        self.updating_from_slider = True
        contrast_value = value / 100.0
        self.contrast_label.setText(f"{contrast_value:.2f}")
        self.updating_from_slider = False
        callback(self.camera_id, value)

    def _on_contrast_edit(self, callback):
        """Handle manual edits to contrast value"""
        if self.updating_from_slider:
            return

        try:
            # Try to parse the decimal value
            contrast_value = float(self.contrast_label.text())
            value = int(round(contrast_value * 100))  # Convert from 0-16.00 to 0-1600

            # Check if value is within range
            if value < 0:
                value = 0
            elif value > self.contrast_slider.maximum():
                value = self.contrast_slider.maximum()

            self.contrast_slider.setValue(value)
            # Note: The slider's valueChanged will trigger the callback
        except ValueError:
            # Restore the previous value if input is invalid
            self.update_contrast_label(self.contrast_slider.value())

    def _on_sharpness_slider_change(self, value, callback):
        """Handle sharpness slider changes and update label"""
        self.updating_from_slider = True
        sharpness_value = value / 100.0
        self.sharpness_label.setText(f"{sharpness_value:.2f}")
        self.updating_from_slider = False
        callback(self.camera_id, value)

    def _on_sharpness_edit(self, callback):
        """Handle manual edits to sharpness value"""
        if self.updating_from_slider:
            return

        try:
            # Try to parse the decimal value
            sharpness_value = float(self.sharpness_label.text())
            value = int(round(sharpness_value * 100))  # Convert from 0-16.00 to 0-1600

            # Check if value is within range
            if value < 0:
                value = 0
            elif value > self.sharpness_slider.maximum():
                value = self.sharpness_slider.maximum()

            self.sharpness_slider.setValue(value)
            # Note: The slider's valueChanged will trigger the callback
        except ValueError:
            # Restore the previous value if input is invalid
            self.update_sharpness_label(self.sharpness_slider.value())
    
    # Value-box updates driven from outside the panel (linked camera, profiles,
    # autofocus). These write the box only - they never re-apply the value.

    def update_fps_label(self, value):
        """Update FPS label"""
        self.updating_from_slider = True
        self.fps_label.setText(str(value))
        self.updating_from_slider = False
    
    def update_exposure_label(self, value):
        """Update exposure label"""
        self.updating_from_slider = True
        self.exposure_label.setText("Auto" if value == 0 else f"{value} µs")
        self.updating_from_slider = False
    
    def update_analog_gain_label(self, value):
        """Update gain label"""
        self.updating_from_slider = True
        if value == 0:
            self.analog_gain_label.setText("Auto")
        else:
            self.analog_gain_label.setText(f"{value:.2f}")
        self.updating_from_slider = False
    
    def update_contrast_label(self, value):
        """Update contrast label"""
        self.updating_from_slider = True
        contrast_value = value / 100.0
        self.contrast_label.setText(f"{contrast_value:.2f}")
        self.updating_from_slider = False

    def update_sharpness_label(self, value):
        """Update sharpness label"""
        self.updating_from_slider = True
        sharpness_value = value / 100.0
        self.sharpness_label.setText(f"{sharpness_value:.2f}")
        self.updating_from_slider = False
    
    def update_lens_position_label(self, value):
        """Update the lens position value box"""
        self.lens_slider[1].setText(f"{value:.2f}")
    
    def update_lens_position_slider(self, value):
        """Update lens position slider without triggering events"""
        self.lens_slider[2].blockSignals(True)
        slider_value = int(value * 100)  # Convert to slider scale
        self.lens_slider[2].setValue(slider_value)
        self.lens_slider[2].blockSignals(False)
    
    def update_exposure_slider_range(self, max_value):
        """Update the maximum value of exposure slider"""
        current_value = self.exposure_slider.value()
        self.exposure_slider.setMaximum(max_value)
        if current_value > max_value and current_value != 0:
            self.exposure_slider.setValue(max_value)
            return max_value
        return None  # No change needed

    def get_current_settings(self):
        """Collect this panel's settings for profile saving.

        Gain, contrast, sharpness and lens position are stored as the real
        values the user sees (0.00-16.00 and 0.00-10.00), not as raw slider
        positions, so a profile stays valid if a slider's resolution changes.
        """
        return {
            "fps": self.fps_slider.value(),
            "exposure": self.exposure_slider.value(),
            "analog_gain": self.analog_gain_slider.value() / 100.0,
            "contrast": self.contrast_slider.value() / 100.0,
            "sharpness": self.sharpness_slider.value() / 100.0,
            "lens_position": self.lens_slider[2].value() / 100.0,
            "resolution_index": self.resolution_dropdown.currentIndex(),
            "resolution_text": self.resolution_dropdown.currentText(),
            "buffer_size": self.buffer_input.text(),
            "quality_index": self.quality_dropdown.currentIndex(),
            "audio_enabled": self.audio_checkbox.isChecked(),
        }

    def set_settings_enabled(self, enabled):
        """Enable or disable the settings that cannot change mid-recording.

        Frame rate, resolution and buffer size either restart the camera or
        break the frame timing the encoder was started with, so they are locked
        while a recording or time-lapse is running. Exposure, gain, contrast,
        sharpness and focus stay live.
        """
        for widget in (self.fps_slider, self.fps_label,
                       self.resolution_dropdown, self.buffer_input):
            widget.setEnabled(enabled)

    def apply_settings(self, settings):
        """Apply settings from a profile.

        Every value is validated and clamped to its widget's range: a
        hand-edited or older profile should fail to apply a single setting, not
        take the whole application down.
        """
        def apply_slider(slider, key, scale=1):
            if key not in settings:
                return
            try:
                value = int(round(float(settings[key]) * scale))
            except (TypeError, ValueError):
                print(f"Profile: ignoring invalid '{key}' value {settings[key]!r}")
                return
            slider.setValue(max(slider.minimum(), min(slider.maximum(), value)))

        apply_slider(self.fps_slider, "fps")
        apply_slider(self.exposure_slider, "exposure")
        # Gain, contrast, sharpness and lens position are stored as their real
        # values; their sliders count hundredths of those.
        apply_slider(self.analog_gain_slider, "analog_gain", 100)
        apply_slider(self.contrast_slider, "contrast", 100)
        apply_slider(self.sharpness_slider, "sharpness", 100)
        apply_slider(self.lens_slider[2], "lens_position", 100)

        if "resolution_index" in settings:
            try:
                index = int(settings["resolution_index"])
            except (TypeError, ValueError):
                index = -1
            if 0 <= index < self.resolution_dropdown.count():
                self.resolution_dropdown.setCurrentIndex(index)
            else:
                # Profiles written by a version with a different resolution list
                # still load as long as the resolution itself still exists.
                match = self.resolution_dropdown.findText(str(settings.get("resolution_text", "")))
                if match >= 0:
                    self.resolution_dropdown.setCurrentIndex(match)
                else:
                    print(f"Profile: resolution index {index} is out of range, keeping current value")

        if "buffer_size" in settings:
            try:
                self.buffer_input.setText(str(int(settings["buffer_size"])))
            except (TypeError, ValueError):
                print(f"Profile: ignoring invalid 'buffer_size' value {settings['buffer_size']!r}")

        if "quality_index" in settings:
            try:
                index = int(settings["quality_index"])
            except (TypeError, ValueError):
                index = -1
            if 0 <= index < self.quality_dropdown.count():
                self.quality_dropdown.setCurrentIndex(index)

        if "audio_enabled" in settings:
            # Normally a JSON bool; strings are accepted so a hand-edited
            # profile with "true" still loads as intended.
            value = settings["audio_enabled"]
            if isinstance(value, str):
                value = value.strip().lower() in ("true", "1", "yes")
            self.audio_checkbox.setChecked(bool(value))

class RecordingControlsPanel:
    """Panel for recording controls"""
    def __init__(self, camera_id):
        self.camera_id = camera_id
        self.layout = QHBoxLayout()
        
        # Will be populated by setup_controls
        self.record_button = None
        self.duration_input = None
        self.snapshot_button = None
        self.timelapse_button = None
    
    def setup_controls(self, record_callback, duration_callback, snapshot_callback,
                       timelapse_callback):
        """Build the record / duration / snapshot / time-lapse row.

        Returns the populated QHBoxLayout for the main window to place.
        """
        self.record_button = QPushButton("Start Recording")
        self.record_button.clicked.connect(lambda: record_callback(self.camera_id))

        self.duration_input = QLineEdit("0")  # 0 = record until stopped
        self.duration_input.setPlaceholderText("Duration")
        self.duration_input.setMaximumWidth(60)
        self.duration_input.textChanged.connect(
            lambda text: duration_callback(self.camera_id, text))

        self.snapshot_button = QPushButton("Snapshot")
        self.snapshot_button.clicked.connect(lambda: snapshot_callback(self.camera_id))

        self.timelapse_button = QPushButton("Time-lapse")
        self.timelapse_button.clicked.connect(lambda: timelapse_callback(self.camera_id))

        self.layout.addWidget(self.record_button)
        self.layout.addWidget(self.duration_input)
        self.layout.addWidget(QLabel("Minutes"))
        self.layout.addWidget(self.snapshot_button)
        self.layout.addWidget(self.timelapse_button)

        return self.layout

    def update_record_button_text(self, is_recording):
        """Update record button text based on recording state"""
        self.record_button.setText("Stop Recording" if is_recording else "Start Recording")

    def update_timelapse_button_text(self, is_active):
        """Update time-lapse button text based on time-lapse state"""
        self.timelapse_button.setText("Stop Time-lapse" if is_active else "Time-lapse")

    def set_snapshot_enabled(self, enabled):
        """Enable or disable the snapshot button"""
        self.snapshot_button.setEnabled(enabled)