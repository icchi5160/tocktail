from dataclasses import replace
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, Slot, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from tocktail.config.settings import Settings
from tocktail.core.summary import DashboardSnapshot
from tocktail.core.history import HistoryReport
from tocktail.ui.formatting import duration_text
from tocktail.ui.popup import Popup
from tocktail.ui.settings_dialog import SettingsDialog
from tocktail.ui.theme import tray_icon
from tocktail.ui.worker import TrackingWorker
from tocktail.ui.dashboard import Dashboard
from tocktail.config import startup


class DesktopController(QObject):
    def __init__(self, app: QApplication, database: Path, worker: TrackingWorker | None = None) -> None:
        super().__init__(app)
        self.app = app
        self.database = database
        self.settings = Settings()
        self.paused = False
        self.quitting = False
        self.error: str | None = None
        self._ready = False
        self._show_on_ready = False
        self._requested_settings: Settings | None = None
        self._dialog_request: Settings | None = None
        self._pending_pause: bool | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.dashboard: Dashboard | None = None
        self.popup = Popup()
        self.popup.setWindowIcon(tray_icon())
        self.worker = worker if worker is not None else TrackingWorker(database)
        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.tray.setToolTip("Tocktail · starting")
        self.menu = QMenu()
        show_action = self.menu.addAction("Open Tocktail")
        show_action.triggered.connect(self.show_popup)
        self.menu.addAction("History dashboard…").triggered.connect(self.show_dashboard_info)
        self.pause_action = self.menu.addAction("Pause tracking")
        self.pause_action.triggered.connect(self.toggle_pause)
        settings_action = self.menu.addAction("Settings…")
        settings_action.triggered.connect(self.show_settings)
        self.startup_action = self.menu.addAction("Start with Windows")
        self.startup_action.setCheckable(True)
        try:
            self.startup_action.setChecked(startup.read() == startup.command(database))
        except (OSError, ValueError, ImportError):
            pass
        self.startup_action.triggered.connect(self.toggle_startup)
        self.menu.addSeparator()
        quit_action = self.menu.addAction("Quit Tocktail")
        quit_action.triggered.connect(self.shutdown)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray_activation)
        self.popup.pause_requested.connect(self.toggle_pause)
        self.popup.settings_requested.connect(self.show_settings)
        self.popup.pin_requested.connect(lambda value: self.request_settings(replace(self.settings, pinned=value)))
        self.popup.top_requested.connect(lambda value: self.request_settings(replace(self.settings, always_on_top=value)))
        self.popup.dashboard_requested.connect(self.show_dashboard_info)
        self.worker.snapshot_ready.connect(self.update_snapshot, Qt.ConnectionType.QueuedConnection)
        self.worker.settings_ready.connect(self.apply_settings, Qt.ConnectionType.QueuedConnection)
        self.worker.failed.connect(self.on_error, Qt.ConnectionType.QueuedConnection)
        if hasattr(self.worker, "history_ready"):
            self.worker.history_ready.connect(self.on_history, Qt.ConnectionType.QueuedConnection)
            self.worker.history_failed.connect(self.on_history_error, Qt.ConnectionType.QueuedConnection)
        self.history_timer = QTimer(self)
        self.history_timer.setInterval(5000)
        self.history_timer.timeout.connect(self.refresh_history)
        self.history_timer.start()
        self.worker.finished.connect(self.on_finished)
        self.app.aboutToQuit.connect(self.wait_for_stop)
        self._enable_controls(False)

    def _enable_controls(self, enabled: bool) -> None:
        self.pause_action.setEnabled(enabled)
        for button in (self.popup.pin_button, self.popup.top_button,
                       self.popup.pause_button, self.popup.settings_button):
            button.setEnabled(enabled)

    def start(self, *, show: bool = False) -> None:
        self._show_on_ready = show
        self.tray.show()
        self.worker.start()

    @Slot(object)
    def apply_settings(self, settings: Settings) -> None:
        if self.quitting or self.error:
            return
        if self.settings_dialog is not None and settings == self._dialog_request:
            self.settings_dialog.accept()
            self._dialog_request = None
        if self._requested_settings is not None:
            if settings != self._requested_settings:
                return
            self._requested_settings = None
        first_settings = not self._ready
        self.settings = settings
        self._ready = True
        self._enable_controls(True)
        self.popup.set_pinned(settings.pinned)
        self.popup.set_topmost(settings.always_on_top)
        if first_settings and (self._show_on_ready or settings.pinned):
            self.show_popup()

    def request_settings(self, settings: Settings) -> None:
        if not self._ready or not self.worker.submit("settings", settings):
            if self.settings_dialog is not None and self.settings_dialog.isVisible():
                self.settings_dialog.show_error("Tracking is busy. Please try again.")
            self.popup.set_pinned(self.settings.pinned)
            self.popup.set_topmost(self.settings.always_on_top)
            return
        self.settings = settings
        self._requested_settings = settings
        # Apply immediately for interaction; durable confirmation comes from worker.
        self.popup.set_pinned(settings.pinned)
        self.popup.set_topmost(settings.always_on_top)

    @Slot(object)
    def update_snapshot(self, snapshot: DashboardSnapshot) -> None:
        if self.quitting or self.error:
            return
        if self._pending_pause is not None:
            if snapshot.paused != self._pending_pause:
                return
            self._pending_pause = None
        self.paused = snapshot.paused
        self.pause_action.setText("Resume tracking" if self.paused else "Pause tracking")
        self.popup.update_snapshot(snapshot)
        if self.popup.isVisible() and not self.popup.property("manuallyPositioned"):
            self.position_popup()
        self.tray.setToolTip(f"Tocktail · {duration_text(snapshot.summary.total_ms)} today"
                             + (" · paused" if self.paused else ""))

    @Slot()
    def toggle_pause(self) -> None:
        if self._ready and self.worker.submit("paused", not self.paused):
            self.paused = not self.paused
            self._pending_pause = self.paused
            self.pause_action.setText("Resume tracking" if self.paused else "Pause tracking")

    @Slot()
    def position_popup(self) -> None:
        if not self.popup.property("manuallyPositioned"):
            anchor = self.tray.geometry().center() if not self.tray.geometry().isEmpty() else QCursor.pos()
            screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                x = min(max(anchor.x() - self.popup.width() // 2, available.left() + 12),
                        available.right() - self.popup.width() - 12)
                y = min(max(anchor.y() - self.popup.height() - 12, available.top() + 12),
                        available.bottom() - self.popup.height() - 12)
                self.popup.move(QPoint(x, y))

    @Slot()
    def show_popup(self) -> None:
        self.popup.adjustSize()
        self.position_popup()
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()

    def on_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.popup.isVisible():
                self.popup.hide()
            elif not self.popup.recently_auto_hidden():
                self.show_popup()

    @Slot()
    def show_settings(self) -> None:
        if not self._ready or self.quitting:
            return
        if self.settings_dialog is not None and self.settings_dialog.isVisible():
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        if not self.popup.pinned:
            self.popup.hide()
        self.settings_dialog = SettingsDialog(self.settings, str(self.database))
        self.settings_dialog.setWindowIcon(tray_icon())
        self.settings_dialog.settings_requested.connect(self.save_dialog_settings)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    @Slot(object)
    def save_dialog_settings(self, settings: Settings) -> None:
        self._dialog_request = settings
        self.request_settings(settings)

    @Slot()
    def show_dashboard_info(self) -> None:
        if not self._ready or self.quitting:
            return
        if self.dashboard is None:
            self.dashboard = Dashboard()
            self.dashboard.setWindowIcon(tray_icon())
            self.dashboard.refresh_requested.connect(self.request_history)
        if not self.popup.pinned:
            self.popup.hide()
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()
        self.dashboard.refresh()

    def request_history(self, start: date, end: date) -> None:
        if not self._ready:
            self.on_history_error("Tracking stopped. Restart Tocktail to refresh history.")
        elif not self.worker.submit("history", (start, end)):
            self.on_history_error("Tracking is busy. Please try again.")

    def refresh_history(self) -> None:
        if self.dashboard is not None and self.dashboard.isVisible() and self._ready:
            self.dashboard.refresh()

    def on_history(self, report: HistoryReport) -> None:
        if self.dashboard is not None and not self.quitting:
            self.dashboard.update_report(report)

    def on_history_error(self, text: str) -> None:
        if self.dashboard is not None:
            self.dashboard.status.setText(text)

    def toggle_startup(self, enabled: bool) -> None:
        try:
            startup.set_enabled(enabled, self.database)
        except (OSError, ValueError) as error:
            self.startup_action.setChecked(not enabled)
            QMessageBox.warning(self.popup, "Windows startup", str(error))

    @Slot(str)
    def on_error(self, message: str) -> None:
        self.error = message
        self.on_history_error("Tracking stopped. Displayed history is no longer live.")
        self._ready = False
        self._enable_controls(False)
        self.popup.status_label.setText("●")
        self.popup.current_state.setText("STOPPED")
        self.popup.current_app.setText("Unable to track")
        self.popup.current_site.clear()
        self.popup.current_site.hide()
        self.popup.current_duration.setText("Details")
        self.popup.current_duration.setToolTip(message)
        self.popup.current_app.setToolTip(message)
        self.popup.status_label.setToolTip(message)
        self.tray.setToolTip("Tocktail · tracking stopped")
        if self.settings_dialog is not None and self.settings_dialog.isVisible():
            self.settings_dialog.show_error(message)
        if not self.quitting:
            self.popup.set_pinned(True)
            self.show_popup()

    @Slot()
    def shutdown(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self._enable_controls(False)
        self.tray.hide()
        self.popup.hide()
        if self.settings_dialog is not None:
            self.settings_dialog.close()
        if self.dashboard is not None:
            self.dashboard.close()
        self.worker.requestInterruption()
        if not self.worker.isRunning():
            self.on_finished()

    @Slot()
    def on_finished(self) -> None:
        if self.quitting:
            self.app.exit(1 if self.error else 0)

    @Slot()
    def wait_for_stop(self) -> None:
        self.worker.requestInterruption()
        self.worker.wait()
        self.tray.hide()
