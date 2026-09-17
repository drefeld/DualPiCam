#!/usr/bin/env python3
"""
Recording controller for DualPiCam.

RecordingWorker owns one camera's H.264 recording session: it brings the camera
to the recording configuration, applies the camera settings, starts and stops
the encoder, and reports progress through Qt signals.

Everything here runs on the Qt main thread. With a QGlPicamera2 preview
attached, picamera2 services stop()/configure() on the Qt event loop, so a
background thread calling them would only wait for the main thread anyway. The
encoder does its work in picamera2's own threads regardless, so no application
thread is needed here.

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

import time
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput

from camera_config import apply_camera_config
from camera_controls import AF_MODE_MANUAL

class RecordingWorker(QObject):
    """
    Runs one camera's recording session and reports its state via Qt signals.
    """
    recording_stopped = pyqtSignal(str, int)  # (camera, duration in seconds)
    recording_error = pyqtSignal(str, str)    # (camera, error message)
    recording_status = pyqtSignal(str, int)   # (camera, elapsed seconds), once per second

    def __init__(self, camera_id, picam, save_path, quality, buffer_count=16,
                 record_audio=False, resolution=None, fps=10, exposure=0,
                 gain=0, contrast=1.0, sharpness=1.0, lens_position=5.0):
        """
        Initialize the recording worker.

        Args:
            camera_id: Camera identifier ('A' or 'B'), included in every emitted signal.
            picam: Picamera2 instance to record from.
            save_path: Full output path for the MP4 file.
            quality: picamera2 Quality enum controlling H264 bitrate.
            buffer_count: DMA buffer count; higher values reduce frame drops at
                the cost of memory. Default 16.
            record_audio: Include an audio track via PyAV. Default False.
            resolution: (width, height) tuple; if None uses the camera's current config.
            fps: Target frame rate. Also passed to the encoder, which sizes the
                bitrate from it and writes it into the MP4 stream header.
            exposure: Exposure time in microseconds; 0 enables auto-exposure.
            gain: Analogue gain (0.0-16.0); 0.0 leaves gain to AE.
            contrast: Contrast (0.0-16.0).
            sharpness: Sharpness (0.0-16.0).
            lens_position: Manual focus position (0.0-10.0). Autofocus is
                disabled during recording to prevent focus drift mid-clip.
        """
        super().__init__()
        self.camera_id = camera_id
        self.picam = picam
        self.save_path = save_path
        self.quality = quality
        self.buffer_count = buffer_count  # Store buffer count from GUI
        self.record_audio = record_audio
        self.resolution = resolution  # Store the explicit resolution

        # Store the camera settings
        self.fps = fps
        self.exposure = exposure
        self.gain = gain
        self.contrast = contrast
        self.sharpness = sharpness
        self.lens_position = lens_position

        self.is_recording = False
        self.start_time = None
        self.elapsed_seconds = 0
        self.encoder = None
        self.output = None

        # Status update timer (main thread)
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._emit_status)

    def start_recording(self):
        """Start recording. Returns True on success, False if it failed.

        On failure, recording_error has already been emitted and the object is
        left in a clean, non-recording state.
        """
        if self.is_recording:
            return False

        try:
            self._prepare_camera()
            self._apply_camera_settings()

            # The encoder derives its bitrate from resolution x framerate and
            # writes the rate into the MP4 stream header, so it needs the real
            # frame rate; left unset it would assume 30 fps.
            self.encoder = H264Encoder(framerate=max(1, int(self.fps)))
            self.encoder.audio = self.record_audio
            self.output = PyavOutput(self.save_path, format="mp4")

            resolution = self.resolution or "current configuration"
            print(f"Camera {self.camera_id}: recording started - {resolution} @ {self.fps} fps")

            self.picam.start_recording(encoder=self.encoder,
                                       output=self.output,
                                       quality=self.quality)
        except Exception as e:
            # start_recording() starts the encoder and then the camera, so a
            # failure part way through can leave the encoder running and the
            # MP4 unfinalised. Tear it down before reporting.
            try:
                self.picam.stop_recording()
            except Exception:
                pass
            self._discard_encoder()
            self.recording_error.emit(self.camera_id, str(e))
            return False

        self.is_recording = True
        self.start_time = time.time()
        self.elapsed_seconds = 0
        self.status_timer.start(1000)  # Update every second
        return True

    def stop_recording(self):
        """Stop recording and finalise the file. Returns the elapsed seconds."""
        if not self.is_recording:
            return self.elapsed_seconds

        self.is_recording = False
        self.status_timer.stop()
        self.elapsed_seconds = int(time.time() - self.start_time) if self.start_time else 0

        try:
            # stop_recording() stops both the encoder and the camera, which is
            # what closes the MP4 container - without it the file has no moov
            # atom and will not play.
            self.picam.stop_recording()
        except Exception as e:
            self.recording_error.emit(self.camera_id, f"Error finishing recording: {e}")
        finally:
            self._discard_encoder()

        self.recording_stopped.emit(self.camera_id, self.elapsed_seconds)
        return self.elapsed_seconds

    def _prepare_camera(self):
        """Bring the camera to the recording configuration.

        This is a no-op when the camera is already configured this way, which
        is the normal case: the preview runs at the resolution and buffer count
        the recording wants, so pressing Record costs no camera restart and
        does not blank the preview.
        """
        if not self.resolution:
            return
        apply_camera_config(self.picam, self.resolution, self.fps, self.buffer_count)

    def _apply_camera_settings(self):
        """Apply all camera settings in a single driver round-trip."""
        controls = {
            "Contrast": self.contrast,
            "Sharpness": self.sharpness,
            "AfMode": AF_MODE_MANUAL,   # Keep autofocus disabled during recording
            "LensPosition": self.lens_position,
        }

        if self.fps > 0:
            frame_time = int(1000000 / self.fps)
            controls["FrameDurationLimits"] = (frame_time, frame_time)

        # AeEnable covers exposure and gain together: as soon as either is
        # manual, AE is off and both have to be sent explicitly.
        if self.exposure == 0 and self.gain == 0:
            controls["AeEnable"] = True
        else:
            controls["AeEnable"] = False
            if self.exposure > 0:
                controls["ExposureTime"] = self.exposure
            if self.gain > 0:
                controls["AnalogueGain"] = self.gain

        self.picam.set_controls(controls)

    def _discard_encoder(self):
        """Drop encoder and output references after a session ends or fails."""
        self.encoder = None
        self.output = None

    def _emit_status(self):
        """Emit recording status updates"""
        if self.is_recording and self.start_time:
            elapsed = int(time.time() - self.start_time)
            self.recording_status.emit(self.camera_id, elapsed)
