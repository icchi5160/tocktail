import argparse
from pathlib import Path
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from tocktail.config.paths import default_database
from tocktail.ui.controller import DesktopController
from tocktail.ui.theme import STYLE, tray_icon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tocktail desktop activity tracker")
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("--show", action="store_true", help="open the popup on startup")
    parser.add_argument("--duration", type=float, help="quit after this many seconds (smoke testing)")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("The desktop tracker requires Windows 11.", file=sys.stderr)
        return 2
    if args.duration is not None and not 0 < args.duration < 86400:
        parser.error("--duration must be between 0 and 86400 seconds")
    app = QApplication.instance() or QApplication(["Tocktail"])
    app.setApplicationName("Tocktail")
    app.setOrganizationName("Tocktail")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(tray_icon())
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Tocktail", "A Windows system tray is required to run Tocktail.")
        return 1
    controller = DesktopController(app, args.db.expanduser().resolve())
    controller.start(show=args.show)
    # Keep Python signal handling responsive while the Qt event loop is idle.
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(250)
    previous_handler = signal.signal(signal.SIGINT, lambda *_: controller.shutdown())
    if args.duration is not None:
        QTimer.singleShot(round(args.duration * 1000), controller.shutdown)
    try:
        return app.exec()
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        controller.wait_for_stop()


if __name__ == "__main__":
    raise SystemExit(main())
