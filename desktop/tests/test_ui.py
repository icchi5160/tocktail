from dataclasses import replace
from datetime import date
import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QWidget

from tocktail.config.settings import Settings
from tocktail.core.models import ActivityState as State, Application, Observation
from tocktail.core.summary import AppUsage, DailySummary, DashboardSnapshot
from tocktail.storage.instance import exclusive_database
from tocktail.storage.sqlite import Store
from tocktail.ui.controller import DesktopController
from tocktail.ui.formatting import application_name, duration_text
from tocktail.ui.popup import Popup
from tocktail.ui.settings_dialog import SettingsDialog
from tocktail.ui.theme import STYLE
from tocktail.ui.worker import TrackingWorker


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(["Tocktail tests"])
    if os.name == "nt":
        font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            assert QFontDatabase.addApplicationFont(str(font_directory / filename)) >= 0
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    yield app


def wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Timed out waiting for worker"


def snapshot():
    return DashboardSnapshot(DailySummary(date(2026, 11, 6), 60000, 30000, 10000,
        (AppUsage("code.exe", "c:/code.exe", 90000),)), State.ACTIVE, "code.exe", 12000)


def test_popup_displays_usage_and_empty_state(qapp):
    popup = Popup()
    popup.update_snapshot(snapshot())
    assert popup.width() == 336
    assert popup.total_label.text() == "1m"
    assert popup.state_values["PASSIVE"].text() == "30s"
    assert popup.current_app.text() == "Visual Studio Code"
    assert popup.current_duration.text().endswith("12s")
    assert popup.app_rows[0][1].toolTip() == "Visual Studio Code"
    assert popup.app_rows[0][3].value() == 1000
    popup.update_snapshot(DashboardSnapshot(DailySummary(date.today()), State.UNTRACKED))
    assert not popup.empty_label.isHidden()
    assert all(row[0].isHidden() for row in popup.app_rows)
    popup.deleteLater()


def test_popup_site_label_updates_clears_and_preserves_compact_size(qapp):
    popup = Popup()
    browser = replace(snapshot(), current_app="chrome.exe", current_domain="github.com")
    popup.update_snapshot(browser)
    assert not popup.current_site.isHidden()
    assert popup.current_site.text() == "github.com"
    assert popup.current_app.text() == "Google Chrome"
    long_domain = "a" * 63 + ".example.com"
    popup.update_snapshot(replace(browser, current_domain=long_domain))
    assert popup.current_site.toolTip() == long_domain
    assert popup.current_site.text() != long_domain
    for cleared in (replace(browser, current_domain=None), replace(browser, paused=True),
                    replace(browser, excluded=True), replace(browser, current_app=None)):
        popup.update_snapshot(cleared)
        assert popup.current_site.isHidden()
        assert popup.current_site.text() == popup.current_site.toolTip() == ""
    popup.update_snapshot(browser)
    popup.show()
    qapp.processEvents()
    assert (popup.width(), popup.height()) == (336, 310)
    popup.hide()
    popup.deleteLater()


def test_pin_and_topmost_are_independent(qapp):
    popup = Popup()
    popup.set_pinned(True)
    assert not popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    popup.set_topmost(True)
    popup.set_pinned(False)
    assert popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    popup.set_topmost(False)
    assert not popup.pinned
    popup.deleteLater()


def test_outside_activation_only_hides_unpinned_popup(qapp):
    popup, other = Popup(), QWidget()
    try:
        popup.set_pinned(True)
        popup.show()
        other.show()
        other.activateWindow()
        QTest.qWait(20)
        popup.hide_if_inactive()
        assert popup.isVisible()
        popup.set_pinned(False)
        popup.hide_if_inactive()
        assert not popup.isVisible()
        assert popup.recently_auto_hidden()
    finally:
        popup.hide()
        other.close()
        popup.deleteLater()
        other.deleteLater()


def test_settings_validation_does_not_emit_invalid_preferences(qapp):
    dialog = SettingsDialog(Settings(), "test.sqlite3")
    requested = []
    dialog.settings_requested.connect(requested.append)
    dialog.excluded_apps.setPlainText("c:/app.exe")
    dialog.request_save()
    assert not requested
    assert not dialog.error_label.isHidden()
    dialog.excluded_apps.setPlainText("chrome.exe\ncode.exe")
    dialog.pinned.setChecked(True)
    dialog.request_save()
    assert requested[0].pinned
    assert requested[0].excluded_apps == ("chrome.exe", "code.exe")
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    dialog.deleteLater()


class FakeMonitor:
    save_titles = False

    def __init__(self):
        self.closed = False
        self.thread_id = None

    def start(self):
        self.thread_id = threading.get_ident()

    def sample(self, source="poll"):
        return Observation(time.time_ns() // 1000000, time.monotonic_ns() // 1000000,
                           Application("editor.exe", "c:/editor.exe"), 42, -1, source=source)

    def wait(self, timeout):
        time.sleep(timeout)
        return False

    def close(self):
        self.closed = True


def test_worker_uses_background_connection_and_commands(qapp, tmp_path):
    monitor = FakeMonitor()
    worker = TrackingWorker(tmp_path / "db", lambda: monitor)
    snapshots, settings, errors = [], [], []
    reports = []
    worker.history_ready.connect(reports.append)
    worker.snapshot_ready.connect(snapshots.append)
    worker.settings_ready.connect(settings.append)
    worker.failed.connect(errors.append)
    worker.start()
    try:
        wait_until(lambda: snapshots or errors)
        assert not errors
        assert monitor.thread_id != threading.get_ident()
        updated = Settings(10, pinned=True)
        assert worker.submit("settings", updated)
        assert worker.submit("paused", True)
        wait_until(lambda: snapshots[-1].paused)
        assert settings[-1] == updated
        assert worker.submit("history", (date.today(), date.today()))
        wait_until(lambda: bool(reports))
        assert reports[-1].start == date.today()
    finally:
        worker.requestInterruption()
        assert worker.wait(5000)
    qapp.processEvents()
    assert monitor.closed
    assert not errors
    with Store(tmp_path / "db") as store:
        assert store.get_setting("desktop")["pinned"] is True
        assert store.connection.execute("SELECT count(*) FROM sessions WHERE is_open=1").fetchone()[0] == 0


def test_worker_rejects_second_writer(qapp, tmp_path):
    path = tmp_path / "db"
    worker = TrackingWorker(path, FakeMonitor)
    errors = []
    worker.failed.connect(errors.append)
    with exclusive_database(path):
        worker.start()
        wait_until(lambda: bool(errors))
        assert worker.wait(5000)
    assert "Another Tocktail" in errors[0]


def test_worker_command_queue_is_bounded(qapp, tmp_path):
    worker = TrackingWorker(tmp_path / "db", FakeMonitor)
    assert all(worker.submit("paused", True) for _ in range(16))
    assert not worker.submit("paused", False)


class FakeWorker(QObject):
    snapshot_ready = Signal(object)
    settings_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.commands = []
        self.stop_requests = 0

    def submit(self, command, value):
        self.commands.append((command, value))
        return True

    def requestInterruption(self):
        self.stop_requests += 1

    def wait(self):
        return True


def test_controller_hide_keeps_tracking_and_routes_preferences(qapp, tmp_path):
    worker = FakeWorker()
    controller = DesktopController(qapp, tmp_path / "db", worker)
    try:
        controller.apply_settings(Settings())
        controller.show_popup()
        controller.popup.pin_button.click()
        assert worker.commands[-1] == ("settings", Settings(pinned=True))
        controller.popup.top_button.click()
        assert worker.commands[-1] == ("settings", Settings(pinned=True, always_on_top=True))
        controller.popup.hide()
        assert worker.stop_requests == 0
        controller.toggle_pause()
        assert worker.commands[-1] == ("paused", True)
    finally:
        controller.popup.hide()
        controller.tray.hide()
        controller.deleteLater()


@pytest.mark.parametrize("duration,expected", [(0, "0m"), (-1000, "0m"), (65000, "1m"), (3660000, "1h 01m")])
def test_duration_formatting(duration, expected):
    assert duration_text(duration) == expected


def test_application_label():
    assert application_name("chrome.exe") == "Google Chrome"


def test_stale_settings_ack_does_not_undo_newer_user_change(qapp, tmp_path):
    worker = FakeWorker()
    controller = DesktopController(qapp, tmp_path / "db", worker)
    try:
        controller.apply_settings(Settings())
        first, latest = Settings(pinned=True), Settings(pinned=True, always_on_top=True)
        controller.request_settings(first)
        controller.request_settings(latest)
        controller.apply_settings(first)
        assert controller.settings == latest
        assert controller.popup.top_button.isChecked()
        controller.apply_settings(latest)
        assert controller._requested_settings is None
    finally:
        controller.popup.hide()
        controller.deleteLater()


def test_stale_pause_snapshot_does_not_undo_requested_pause(qapp, tmp_path):
    controller = DesktopController(qapp, tmp_path / "db", FakeWorker())
    try:
        controller.apply_settings(Settings())
        controller.toggle_pause()
        controller.update_snapshot(snapshot())
        assert controller.paused
        controller.update_snapshot(replace(snapshot(), paused=True, state=State.PAUSED))
        assert controller._pending_pause is None
        assert controller.popup.pause_button.text() == "Resume"
    finally:
        controller.popup.hide()
        controller.deleteLater()


def test_settings_dialog_waits_for_durable_save_ack(qapp, tmp_path):
    controller = DesktopController(qapp, tmp_path / "db", FakeWorker())
    try:
        controller.apply_settings(Settings(pinned=True))
        controller.show_settings()
        dialog = controller.settings_dialog
        dialog.idle_seconds.setValue(60)
        dialog.request_save()
        assert dialog.isVisible()
        controller.apply_settings(Settings(60, pinned=True))
        assert not dialog.isVisible()
    finally:
        controller.popup.hide()
        if controller.settings_dialog:
            controller.settings_dialog.hide()
        controller.deleteLater()


def test_dashboard_ignores_stale_selection_and_displays_history(qapp):
    from tocktail.core.history import HistoryReport, HistorySession, SiteUsage
    from tocktail.ui.dashboard import Dashboard
    dialog = Dashboard()
    start, end = dialog.interval()
    report = HistoryReport(start, end, (("ACTIVE", 60000), ("IDLE", 5000)),
        (AppUsage("chrome.exe", "c:/chrome.exe", 60000),), (SiteUsage("example.com", 60000),),
        ((start, 60000),), (HistorySession(1000, 61000, "ACTIVE", "chrome.exe", "example.com", False),))
    dialog.update_report(report)
    assert dialog.total.text() == "1m"
    assert dialog.sites.item(0, 0).text() == "example.com"
    assert dialog.apps.item(0, 0).text() == "Google Chrome"
    assert dialog.sessions.rowCount() == 1
    assert dialog.chart.days == report.days
    dialog.update_report(replace(report, start=end, applications=(), sites=(), sessions=()))
    assert dialog.sites.rowCount() == 1  # Stale result did not clear the current view.
    requested = []
    dialog.refresh_requested.connect(lambda a, b: requested.append((a,b)))
    dialog.refresh()
    assert requested == [(start,end)]
    dialog.close()
    dialog.deleteLater()


def test_controller_opens_real_dashboard_and_requests_history(qapp, tmp_path):
    worker = FakeWorker()
    controller = DesktopController(qapp, tmp_path / "db", worker)
    try:
        controller.apply_settings(Settings())
        controller.show_dashboard_info()
        assert controller.dashboard.isVisible()
        assert worker.commands[-1][0] == "history"
        controller.dashboard.close()
        count = len(worker.commands)
        controller.refresh_history()
        assert len(worker.commands) == count
    finally:
        if controller.dashboard:
            controller.dashboard.close()
        controller.popup.hide()
        controller.tray.hide()
        controller.history_timer.stop()
