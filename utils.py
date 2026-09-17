#!/usr/bin/env python3
"""
Shared constants and UI utility functions for DualPiCam.

Holds the application version and factory helpers for creating reusable Qt
widgets: horizontal sliders, a lens-position slider with an editable value box
and autofocus button, and profile management controls (dropdown + load/save
buttons).

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

from PyQt5.QtWidgets import (QSlider, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                            QComboBox, QInputDialog, QLineEdit, QMessageBox)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon

# Shown in the window title and written into every saved profile.
APP_VERSION = "1.0.0"


def create_slider(min_val, max_val, default_val, callback):
    """Create a horizontal slider with given parameters"""
    slider = QSlider(Qt.Horizontal)
    slider.setMinimum(min_val)
    slider.setMaximum(max_val)
    slider.setValue(default_val)
    slider.valueChanged.connect(callback)
    return slider

def create_labeled_slider(camera, label_text, default_val, slider_callback, autofocus_callback):
    """Create a slider with an editable value box and an autofocus button.

    default_val is on the slider's raw 0-1000 scale (0.00-10.00 lens position).
    """
    slider_layout = QVBoxLayout()

    # Text label plus an editable box showing the current value, same pattern
    # as the other camera controls (FPS, exposure, gain, ...).
    header_layout = QHBoxLayout()
    header_layout.setSpacing(5)
    header_layout.addWidget(QLabel(f"{label_text} {camera}:"))
    value_edit = QLineEdit(f"{default_val/100.0:.2f}")
    value_edit.setMaximumWidth(60)
    value_edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    header_layout.addWidget(value_edit)
    header_layout.addWidget(QLabel("/ 10"))
    header_layout.addStretch(1)
    slider_layout.addLayout(header_layout)
    slider_layout.setContentsMargins(0, 0, 0, 2)  # Reduce bottom margin to bring label closer to slider

    # Create slider and autofocus button layout
    control_layout = QHBoxLayout()

    # Create slider - increased precision (0-1000 range for 0.00-10.00)
    slider = QSlider(Qt.Horizontal)
    slider.setMinimum(0)
    slider.setMaximum(1000)  # 100x more granular
    slider.setValue(default_val)  # Convert default value to new scale

    # Guards re-entrancy between the slider and the text box, same approach
    # used by CameraControlsPanel's other editable slider labels.
    state = {"updating_from_slider": False}

    def on_slider_value_changed(value):
        state["updating_from_slider"] = True
        value_edit.setText(f"{value/100.0:.2f}")
        state["updating_from_slider"] = False
        slider_callback(camera, value/100.0)

    slider.valueChanged.connect(on_slider_value_changed)

    def on_value_edited():
        if state["updating_from_slider"]:
            return
        try:
            typed_value = float(value_edit.text())
        except ValueError:
            value_edit.setText(f"{slider.value()/100.0:.2f}")
            return
        typed_value = max(0.0, min(10.0, typed_value))
        slider.setValue(int(round(typed_value * 100)))
        # slider.valueChanged (above) applies the value and updates the box

    value_edit.editingFinished.connect(on_value_edited)

    # Create autofocus button
    autofocus_button = QPushButton("Autofocus")
    autofocus_button.clicked.connect(lambda: autofocus_callback(camera))

    control_layout.addWidget(slider)
    control_layout.addWidget(autofocus_button)
    control_layout.setContentsMargins(0, 0, 0, 0)  # No margins to keep things tight

    slider_layout.addLayout(control_layout)

    return slider_layout, value_edit, slider

def create_profile_controls(load_callback, save_callback):
    """Create dropdown and buttons for profile management"""
    profile_layout = QHBoxLayout()
    
    # Create dropdown for profile selection
    profile_dropdown = QComboBox()
    profile_dropdown.setMinimumWidth(200)
    
    # Create load and save buttons with icons
    load_button = QPushButton()
    load_button.setIcon(QIcon.fromTheme("go-up", QIcon.fromTheme("edit-undo")))
    load_button.setToolTip("Load selected profile")
    load_button.clicked.connect(lambda: load_callback(profile_dropdown.currentText()))
    
    save_button = QPushButton()
    save_button.setIcon(QIcon.fromTheme("document-save", QIcon.fromTheme("edit-save")))
    save_button.setToolTip("Save current settings as profile")
    save_button.clicked.connect(save_callback)
    
    # Add widgets to layout
    profile_layout.addWidget(QLabel("Profile:"))
    profile_layout.addWidget(profile_dropdown, 1)  # Give the dropdown more space
    profile_layout.addWidget(load_button)
    profile_layout.addWidget(save_button)
    
    return profile_layout, profile_dropdown, load_button, save_button

def get_profile_name_dialog(parent, existing_profiles):
    """Show dialog to get profile name for saving"""
    dialog = QInputDialog(parent)
    dialog.setWindowTitle("Save Profile")
    dialog.setLabelText("Enter profile name:")
    dialog.setOkButtonText("Save")
    dialog.setCancelButtonText("Cancel")
    dialog.setTextEchoMode(QLineEdit.Normal)
    
    if dialog.exec_() == QInputDialog.Accepted:
        name = dialog.textValue()
        if name:
            if name in existing_profiles:
                # Simpler overwrite dialog - just Yes/No
                reply = QMessageBox.question(
                    parent, 
                    "Profile Exists",
                    f"Profile '{name}' already exists. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No  # Default to No to prevent accidental overwrite
                )
                if reply == QMessageBox.Yes:
                    return name
                else:
                    # Let the user try again
                    return get_profile_name_dialog(parent, existing_profiles)
            else:
                return name
    return None