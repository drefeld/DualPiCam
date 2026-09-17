#!/usr/bin/env python3
"""
Application entry point for DualPiCam.

Creates the QApplication and MainWindow, then starts the Qt event loop.

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

from PyQt5.QtWidgets import QApplication, QMessageBox
from gui_main import MainWindow
from utils import APP_VERSION
import sys
import traceback


def main():
    """Create the application window and run the Qt event loop."""
    app = QApplication(sys.argv)
    app.setApplicationName("DualPiCam")
    app.setApplicationVersion(APP_VERSION)

    try:
        window = MainWindow()
    except Exception as error:
        # Most start-up failures are a camera that is missing, unseated or still
        # held by another process. Show that instead of a bare traceback in a
        # terminal the user may not even be looking at.
        traceback.print_exc()
        QMessageBox.critical(None, "DualPiCam - Startup Failed",
                             f"DualPiCam could not start:\n\n{error}")
        return 1

    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())