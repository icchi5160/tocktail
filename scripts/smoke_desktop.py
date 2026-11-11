"""Exercise the real Windows tray/worker lifecycle using a separate test DB."""

import argparse
import json
from pathlib import Path
import tempfile

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from tocktail.config.settings import Settings
from tocktail.storage.instance import exclusive_database
from tocktail.storage.sqlite import Store
from tocktail.ui.controller import DesktopController
from tocktail.ui.theme import STYLE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--duration", type=float, default=8)
    args = parser.parse_args()
    if not 3 <= args.duration <= 300:
        parser.error("duration must be between 3 and 300 seconds")
    if args.db is None:
        root = Path(".smoke")
        root.mkdir(exist_ok=True)
        args.db = Path(tempfile.mkdtemp(prefix="desktop-", dir=root)) / "test.sqlite3"
    database = args.db.resolve()
    if database.exists():
        parser.error("use a new test database; existing history will not be modified")
    database.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_database(database), Store(database) as store:
        store.set_settings({"desktop": Settings(pinned=True).to_dict(), "idle_seconds": 300})
    app = QApplication(["Tocktail smoke test"])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        raise RuntimeError("A real Windows desktop with a system tray is required")
    controller = DesktopController(app, database)
    controller.start(show=True)
    QTimer.singleShot(750, controller.show_settings)

    def report() -> None:
        print(json.dumps({
            "platform": app.platformName(), "popup_visible": controller.popup.isVisible(),
            "pinned": controller.popup.pinned, "tray_visible": controller.tray.isVisible(),
            "settings_visible": bool(controller.settings_dialog and controller.settings_dialog.isVisible()),
            "worker_running": controller.worker.isRunning(), "error": controller.error,
            "background_alpha": controller.popup.grab().toImage().pixelColor(20, 80).alpha(),
        }), flush=True)

    QTimer.singleShot(2000, report)
    QTimer.singleShot(round(args.duration * 1000), controller.shutdown)
    exit_code = app.exec()
    controller.wait_for_stop()
    with Store(database) as store:
        open_sessions = store.connection.execute("SELECT count(*) FROM sessions WHERE is_open=1").fetchone()[0]
        overlaps = store.connection.execute(
            "SELECT count(*) FROM sessions a JOIN sessions b ON a.id<b.id "
            "AND a.started_at<a.ended_at AND b.started_at<b.ended_at "
            "AND a.started_at<b.ended_at AND b.started_at<a.ended_at"
        ).fetchone()[0]
        print(json.dumps({"open_sessions": open_sessions, "overlaps": overlaps,
                          "integrity": store.connection.execute("PRAGMA integrity_check").fetchone()[0]}), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
