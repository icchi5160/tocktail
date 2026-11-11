"""Render deterministic UI previews without recording any real activity."""

import argparse
from datetime import date
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".smoke/phase2-popup.png"))
    parser.add_argument("--settings", action="store_true")
    parser.add_argument("--dashboard", action="store_true")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_SCALE_FACTOR", "2")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from tocktail.config.settings import Settings
    from tocktail.core.models import ActivityState
    from tocktail.core.summary import AppUsage, DailySummary, DashboardSnapshot
    from tocktail.ui.popup import Popup
    from tocktail.ui.settings_dialog import SettingsDialog
    from tocktail.ui.theme import STYLE

    app = QApplication([])
    # The offscreen platform has no native Windows font discovery.
    if os.name == "nt":
        font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            if QFontDatabase.addApplicationFont(str(font_directory / filename)) < 0:
                raise RuntimeError(f"Could not load preview font: {filename}")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if args.dashboard:
        from tocktail.core.history import HistoryReport, HistorySession, SiteUsage
        from tocktail.ui.dashboard import Dashboard
        from datetime import timedelta
        widget = Dashboard()
        start, end = widget.interval()
        days = tuple((start + timedelta(days=i), value * 60000)
                     for i, value in enumerate((110, 245, 187, 96, 265, 180, 212)))
        total = sum(value for _, value in days)
        widget.update_report(HistoryReport(start, end, (("ACTIVE", total-1800000), ("PASSIVE", 1800000), ("IDLE", 5400000)),
            (AppUsage("code.exe", "c:/code.exe", total*6//10), AppUsage("chrome.exe", "c:/chrome.exe", total*3//10),
             AppUsage("windowsterminal.exe", "c:/terminal.exe", total//10)),
            (SiteUsage("github.com", total//5), SiteUsage("example.com", total//10)), days,
            (HistorySession(1793926800000, 1793926860000, "ACTIVE", "chrome.exe", "github.com", False),)))
    elif args.settings:
        widget = SettingsDialog(Settings(pinned=True), "%LOCALAPPDATA%/Tocktail/tocktail.sqlite3")
    else:
        widget = Popup()
        widget.set_pinned(True)
        summary = DailySummary(date(2026, 11, 6), 8_040_000, 1_680_000, 2_580_000, (
            AppUsage("code.exe", "c:/demo/code.exe", 4_380_000),
            AppUsage("chrome.exe", "c:/demo/chrome.exe", 2_220_000),
            AppUsage("figma.exe", "c:/demo/figma.exe", 1_380_000),
            AppUsage("windowsterminal.exe", "c:/demo/terminal.exe", 1_020_000),
            AppUsage("explorer.exe", "c:/demo/explorer.exe", 720_000),
        ))
        widget.update_snapshot(DashboardSnapshot(summary, ActivityState.ACTIVE, "chrome.exe", 1_440_000,
                                                  current_domain="github.com"))
    widget.show()
    app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(args.output)):
        raise RuntimeError("Could not save preview")
    print(f"Saved {args.output} ({widget.width()} x {widget.height()} logical pixels)")
    widget.hide()


if __name__ == "__main__":
    main()
