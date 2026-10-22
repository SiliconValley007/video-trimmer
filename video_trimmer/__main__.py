"""Application entry point: ``python -m video_trimmer``."""
import os

DEBUG = bool(os.environ.get("VIDEO_TRIMMER_DEBUG"))
if not DEBUG:  # must happen before any PyQt6 module is imported
    os.environ["QT_LOGGING_RULES"] = "*=false"

import sys  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

from . import __version__  # noqa: E402
from .quiet import silence_native_stderr  # noqa: E402

from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from .gui import MainWindow  # noqa: E402
from .style import STYLE  # noqa: E402


def _excepthook(exc_type, exc, tb) -> None:
    traceback.print_exception(exc_type, exc, tb)
    if QApplication.instance():
        QMessageBox.critical(None, "Unexpected error", f"{exc_type.__name__}: {exc}")


def main() -> int:
    if not DEBUG:
        silence_native_stderr()  # hides FFmpeg/MFT chatter from native libraries
    app = QApplication(sys.argv)
    app.setApplicationName("Video Trimmer")
    app.setApplicationVersion(__version__)
    icon = Path(__file__).parent / "assets" / "icon.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    sys.excepthook = _excepthook
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
