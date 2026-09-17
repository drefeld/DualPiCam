#!/usr/bin/env python3
"""
Main application window for DualPiCam.

Assembles the complete Qt UI from reusable panel components (CameraControlsPanel,
RecordingControlsPanel) and wires them to the CameraManager controller. Also
owns the GL preview widgets, status indicators, FPS counter timer, and the
dark-theme stylesheet.

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

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                           QLineEdit, QSizePolicy, QCheckBox, QPushButton)
from PyQt5.QtCore import QTimer, QTime, Qt
from picamera2.previews.qt import QGlPicamera2
from camera_config import create_camera_instances, configure_camera
from camera_controls import AutofocusHandler
from app_controller import CameraManager
from gui_components import CameraControlsPanel, RecordingControlsPanel
from utils import APP_VERSION, create_profile_controls
import os

class StatusIndicator(QLabel):
    """A coloured dot shown next to a preview while a session is running.

    Red marks an active recording, blue an active time-lapse. Toggle it with
    setVisible(); it starts hidden.
    """
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.setFixedSize(15, 15)
        self.setStyleSheet(f"background-color: {color}; border-radius: 7px;")
        self.hide()

class MainWindow(QWidget):
    """
    Main application window for DualPiCam.

    Houses the complete UI: camera control panels, live OpenGL previews,
    recording controls, and status indicators for both cameras (A and B).
    Delegates all camera operations to CameraManager and uses
    CameraControlsPanel / RecordingControlsPanel for per-camera UI sections.
    """

    def __init__(self):
        """
        Initialize the main window and all sub-components.

        Initialization order matters: camera instances and CameraManager are
        created before the UI panels so that the callbacks passed to those
        panels are already bound. Cameras are configured and started last,
        after all UI elements exist.
        """
        super().__init__()
        self.setWindowTitle(f"DualPiCam {APP_VERSION}")
        
        # Initialize camera instances
        self.picam2a, self.picam2b = create_camera_instances()
        
        # Main layout
        main_layout = QVBoxLayout()

        # Flag to prevent feedback loops during linked camera updates
        self.updating_linked_control = False

        # Initialize controller before UI setup. CameraManager owns the camera
        # state (fps, resolution, buffer count, quality, ...); this window reads
        # it from there rather than keeping a second copy that can drift.
        self.controller = CameraManager(self.picam2a, self.picam2b, self)
        
        # Initialize autofocus handler after controller
        self.autofocus_handler = AutofocusHandler(self.picam2a, self.picam2b)
        self.autofocus_handler.autofocus_done.connect(self.controller.update_lens_position)
        
        # Build UI components with combined header
        self._setup_combined_header(main_layout)
        self._setup_save_locations(main_layout)
        self._setup_camera_controls(main_layout)
        self._setup_previews(main_layout)
        self._setup_recording_controls(main_layout)
        
        # Timing for the FPS display
        self.time = QTime()
        self.time.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_framerate)
        self.timer.start(1000)

        # Set callbacks for frame capture directly to the controller (skips a delegation hop)
        self.picam2a.post_callback = self.controller.on_frame_a
        self.picam2b.post_callback = self.controller.on_frame_b

        # Both cameras default to the app's own Recordings directory. The
        # controller holds the authoritative copy of each save location; the
        # text boxes are seeded from it here and write back to it when edited.
        default_save_location = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Recordings")
        os.makedirs(default_save_location, exist_ok=True)

        for box in (self.save_location_input_a, self.save_location_input_b):
            box.setText(default_save_location)
        self.controller.save_location_a = default_save_location
        self.controller.save_location_b = default_save_location

        # Configure cameras with callbacks, using the controller's camera state
        configure_camera(self.picam2a,
                         self.controller.current_resolution_a,
                         self.controller.current_fps_a,
                         self.controller.current_buffer_size_a,
                         post_callback=self.controller.on_frame_a)
        configure_camera(self.picam2b,
                         self.controller.current_resolution_b,
                         self.controller.current_fps_b,
                         self.controller.current_buffer_size_b,
                         post_callback=self.controller.on_frame_b)

        # Set main layout
        self.setLayout(main_layout)
        
        # Load profile list
        self.controller.refresh_profiles()
        
        # Apply dark theme
        self.apply_dark_theme()
        
        # Start cameras
        self.picam2a.start()
        self.picam2b.start()
        
        # IMPORTANT: Set window maximized at the end
        self.showMaximized()
    
    def _setup_combined_header(self, main_layout):
        """Set up combined header with camera toggles and profile controls"""
        header_layout = QHBoxLayout()
        
        # Camera toggle controls (left section)
        camera_toggles_layout = QHBoxLayout()
        
        # Camera A toggle
        cam_a_layout = QHBoxLayout()
        self.camera_a_label = QLabel("Camera A")
        self.camera_a_checkbox = QCheckBox()
        self.camera_a_checkbox.setChecked(True)
        self.camera_a_checkbox.stateChanged.connect(lambda state: self.toggle_camera('A', state))
        cam_a_layout.addWidget(self.camera_a_label)
        cam_a_layout.addWidget(self.camera_a_checkbox)
        cam_a_layout.setContentsMargins(0, 0, 10, 0)  # Add some spacing to the right
        
        # Camera B toggle
        cam_b_layout = QHBoxLayout()
        self.camera_b_label = QLabel("Camera B")
        self.camera_b_checkbox = QCheckBox()
        self.camera_b_checkbox.setChecked(True)
        self.camera_b_checkbox.stateChanged.connect(lambda state: self.toggle_camera('B', state))
        cam_b_layout.addWidget(self.camera_b_label)
        cam_b_layout.addWidget(self.camera_b_checkbox)
        cam_b_layout.setContentsMargins(0, 0, 10, 0)  # Add some spacing to the right
        
        # Link cameras toggle
        link_layout = QHBoxLayout()
        self.link_cameras_label = QLabel("Link Cameras")
        self.link_cameras_checkbox = QCheckBox()
        self.link_cameras_checkbox.setChecked(True)
        self.link_cameras_checkbox.stateChanged.connect(self.on_link_cameras_changed)
        link_layout.addWidget(self.link_cameras_label)
        link_layout.addWidget(self.link_cameras_checkbox)
        
        # Add camera toggles to the layout
        camera_toggles_layout.addLayout(cam_a_layout)
        camera_toggles_layout.addLayout(cam_b_layout)
        camera_toggles_layout.addLayout(link_layout)
        camera_toggles_layout.addStretch(1)  # Push everything to the left
        
        # Create profile controls (right section)
        profile_controls = create_profile_controls(
            self.controller.load_profile,
            self.controller.save_profile
        )
        self.profile_dropdown = profile_controls[1]
        
        # Add camera toggles (left) and profile controls (right)
        header_layout.addLayout(camera_toggles_layout, 1)  # Camera toggles get space on left
        header_layout.addLayout(profile_controls[0], 3)  # Profile controls get more space on right
        
        main_layout.addLayout(header_layout)
    
    def _setup_save_locations(self, main_layout):
        """Set up the per-camera save location row.

        The path box is editable: typing a path applies it directly, Choose...
        picks one through a file dialog, and Open Folder shows the current
        directory in the system file browser.
        """
        save_layout = QHBoxLayout()

        self.save_location_input_a = QLineEdit()
        self.save_location_input_b = QLineEdit()

        for camera, box in (('A', self.save_location_input_a),
                            ('B', self.save_location_input_b)):
            box.editingFinished.connect(
                lambda cam=camera: self.controller.on_save_location_edited(cam))

            choose_button = QPushButton("Choose...")
            choose_button.clicked.connect(
                lambda _, cam=camera: self.select_save_location(cam))
            open_button = QPushButton("Open Folder")
            open_button.clicked.connect(
                lambda _, cam=camera: self.controller.open_save_location(cam))

            camera_layout = QHBoxLayout()
            camera_layout.addWidget(QLabel(f"Save Location for Camera {camera}:"))
            camera_layout.addWidget(box, 3)  # 3:1:1 against the two buttons
            camera_layout.addWidget(choose_button, 1)
            camera_layout.addWidget(open_button, 1)
            save_layout.addLayout(camera_layout)

        main_layout.addLayout(save_layout)
    
    def _setup_camera_controls(self, main_layout):
        """Set up camera control panels"""
        controls_layout = QHBoxLayout()

        # Available resolutions
        resolutions = [
            "640x360 (16:9)", "640x480 (4:3)", "800x600 (4:3)",
            "960x540 (16:9)", "1024x768 (4:3)", "1280x720 (16:9)",
            "1400x1050 (4:3)", "1440x1080 (4:3)", "1600x900 (16:9)",
            "1600x1200 (4:3)", "1920x1080 (16:9)", "2048x1152 (16:9)",
            "2048x1536 (4:3)", "2560x1440 (16:9)",
            "3840x2160 (16:9)", "4096x2304 (Max)",
        ]

        self.control_panel_a = CameraControlsPanel('A', self.controller.current_fps_a)
        self.control_panel_b = CameraControlsPanel('B', self.controller.current_fps_b)

        for panel in (self.control_panel_a, self.control_panel_b):
            controls_layout.addLayout(panel.setup_controls(
                self.controller.on_fps_slider_change,
                self.controller.on_exposure_slider_change,
                self.controller.on_analog_gain_slider_change,
                self.controller.on_contrast_slider_change,
                self.controller.on_sharpness_slider_change,
                self.controller.on_resolution_change,
                self.controller.on_buffer_size_changed,
                self.controller.on_quality_change,
                self.controller.on_audio_option_changed,
                self.controller.on_lens_slider_change,
                self.controller.autofocus,
                resolutions,
            ))

        main_layout.addLayout(controls_layout)
    
    def _setup_previews(self, main_layout):
        """Set up the two live previews and their status rows.

        Under each preview sits an FPS readout on the left and, on the right,
        the recording (red) and time-lapse (blue) indicators followed by the
        elapsed-time and time-lapse status text.
        """
        preview_layout = QHBoxLayout()

        self.preview_a = QGlPicamera2(self.picam2a, width=960, height=540)
        self.preview_b = QGlPicamera2(self.picam2b, width=960, height=540)

        self.recording_indicator_a = StatusIndicator("red")
        self.recording_indicator_b = StatusIndicator("red")
        self.timelapse_indicator_a = StatusIndicator("blue")
        self.timelapse_indicator_b = StatusIndicator("blue")

        self.fps_label_a = QLabel("Camera A FPS: Initializing...")
        self.fps_label_b = QLabel("Camera B FPS: Initializing...")
        self.recording_duration_label_a = QLabel("00:00:00")
        self.recording_duration_label_b = QLabel("00:00:00")
        self.timelapse_status_label_a = QLabel("")
        self.timelapse_status_label_b = QLabel("")

        cameras = (
            (self.preview_a, self.fps_label_a, self.recording_indicator_a,
             self.timelapse_indicator_a, self.recording_duration_label_a,
             self.timelapse_status_label_a),
            (self.preview_b, self.fps_label_b, self.recording_indicator_b,
             self.timelapse_indicator_b, self.recording_duration_label_b,
             self.timelapse_status_label_b),
        )

        for (preview, fps_label, rec_indicator,
             tl_indicator, duration_label, tl_status_label) in cameras:
            preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            preview.setMinimumSize(320, 240)
            fps_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

            status_layout = QHBoxLayout()
            status_layout.addWidget(rec_indicator)
            status_layout.addWidget(tl_indicator)
            status_layout.addWidget(duration_label)
            status_layout.addWidget(tl_status_label)
            status_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            info_layout = QHBoxLayout()
            info_layout.addWidget(fps_label, alignment=Qt.AlignLeft)
            info_layout.addLayout(status_layout)

            camera_layout = QVBoxLayout()
            camera_layout.addWidget(preview)
            camera_layout.addLayout(info_layout)
            preview_layout.addLayout(camera_layout)

        main_layout.addLayout(preview_layout)
    
    def _setup_recording_controls(self, main_layout):
        """Set up recording controls"""
        recording_layout = QHBoxLayout()

        self.recording_controls_a = RecordingControlsPanel('A')
        self.recording_controls_b = RecordingControlsPanel('B')

        for panel in (self.recording_controls_a, self.recording_controls_b):
            recording_layout.addLayout(panel.setup_controls(
                self.controller.on_record_button_click,
                self.controller.on_recording_duration_changed,
                self.controller.take_snapshot,
                self.controller.on_timelapse_button_click,
            ))

        main_layout.addLayout(recording_layout)
    
    # Delegate methods to controller
    
    def toggle_camera(self, camera, state):
        """Delegate to controller's toggle_camera method"""
        self.controller.toggle_camera(camera, state)
        
    def on_link_cameras_changed(self, state):
        """Delegate to controller's on_link_cameras_changed method"""
        self.controller.on_link_cameras_changed(state)
        
    def select_save_location(self, camera):
        """Delegate to controller's select_save_location method"""
        self.controller.select_save_location(camera)
        
    def update_framerate(self):
        """Update FPS directly in the main thread"""
        self.controller.update_framerate()
    
    def restart_preview_a(self):
        """Restart preview for camera A"""
        if self.camera_a_checkbox.isChecked():
            self.picam2a.start()
    
    def restart_preview_b(self):
        """Restart preview for camera B"""
        if self.camera_b_checkbox.isChecked():
            self.picam2b.start()
    
    def apply_dark_theme(self):
        """Apply dark theme to the application"""
        self.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                color: #f0f0f0;
                font-size: 12px;
            }
            QPushButton {
                background-color: #3c3c3c;
                color: #f0f0f0;
                border: 1px solid #5c5c5c;
                padding: 5px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #4c4c4c;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QLineEdit {
                background-color: #3c3c3c;
                color: #f0f0f0;
                border: 1px solid #5c5c5c;
                padding: 3px;
                border-radius: 2px;
            }
            QComboBox {
                background-color: #3c3c3c;
                color: #f0f0f0;
                border: 1px solid #5c5c5c;
                padding: 3px;
                border-radius: 2px;
            }
            QComboBox QAbstractItemView {
                background-color: #3c3c3c;
                color: #f0f0f0;
                selection-background-color: #4c4c4c;
            }
            QSlider {
                height: 20px;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #3c3c3c;
                margin: 0px;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #00c1ff;
                width: 15px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #00c1ff;
                border-radius: 2px;
            }
            QCheckBox {
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
            }
            QCheckBox::indicator:unchecked {
                background-color: #3c3c3c;
                border: 1px solid #5c5c5c;
                border-radius: 2px;
            }
            QCheckBox::indicator:checked {
                background-color: #00c1ff;
                border: 1px solid #5c5c5c;
                border-radius: 2px;
            }
            QLabel {
                color: #f0f0f0;
            }
        """)
    
    def closeEvent(self, event):
        """Handle application close"""
        self.timer.stop()

        # Finish recordings and time-lapse sessions through their normal paths,
        # so the MP4 containers are closed and both log files are completed.
        self.controller.shutdown()

        for picam in (self.picam2a, self.picam2b):
            try:
                picam.stop()
            except Exception as e:
                print(f"Error stopping camera: {e}")

        event.accept()