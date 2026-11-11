import time

from PySide6.QtCore import QEvent, QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)

from tocktail.core.summary import DashboardSnapshot
from tocktail.ui.formatting import application_name, duration_text
from tocktail.ui.theme import COMPACT_POPUP_STYLE, tray_icon


class DragHeader(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._offset: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._offset = event.globalPosition().toPoint() - self.window().pos()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._offset)
            self.window().setProperty("manuallyPositioned", True)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._offset = None


class StateBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.values = (0, 0, 0)
        self.setFixedHeight(5)

    def paintEvent(self, event: QEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eceef5"))
        painter.drawRoundedRect(QRectF(self.rect()), 2, 2)
        total = sum(self.values)
        if total:
            x = 0.0
            for value, color in zip(self.values, ("#7060d5", "#b6a9ec", "#cdd2e1")):
                width = self.width() * value / total
                painter.setBrush(QColor(color))
                painter.drawRect(QRectF(x, 0, width, self.height()))
                x += width


def label(text: str, name: str = "", *, wrap: bool = False) -> QLabel:
    item = QLabel(text)
    item.setObjectName(name)
    item.setWordWrap(wrap)
    item.setTextFormat(Qt.TextFormat.PlainText)
    return item


class Popup(QWidget):
    pin_requested = Signal(bool)
    top_requested = Signal(bool)
    settings_requested = Signal()
    pause_requested = Signal()
    dashboard_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("popup")
        self.setWindowTitle("Tocktail")
        self.setAccessibleName("Tocktail activity summary")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedSize(336, 310)
        self.setStyleSheet(COMPACT_POPUP_STYLE)
        self.pinned = False
        self._last_auto_hide = float("-inf")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(5)

        header = DragHeader(self)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        mark = label("")
        mark.setPixmap(tray_icon().pixmap(18, 18))
        row.addWidget(mark)
        row.addWidget(label("Tocktail", "brand"))
        row.addStretch()
        self.pin_button = self._tool("Pin", "Pin popup", checkable=True)
        self.top_button = self._tool("Top", "Always on top", checkable=True)
        close = self._tool("×", "Hide popup")
        row.addWidget(self.pin_button)
        row.addWidget(self.top_button)
        row.addWidget(close)
        self.pin_button.toggled.connect(self.pin_requested)
        self.top_button.toggled.connect(self.top_requested)
        close.clicked.connect(self.hide)
        layout.addWidget(header)

        today = QHBoxLayout()
        today_caption = QVBoxLayout()
        today_caption.setSpacing(0)
        self.day_label = label("TODAY", "eyebrow")
        self.total_label = label("0m", "total")
        today_caption.addWidget(self.day_label)
        today_caption.addWidget(label("Active + passive time", "muted"))
        today.addLayout(today_caption)
        today.addStretch()
        today.addWidget(self.total_label)
        layout.addLayout(today)

        breakdown = QHBoxLayout()
        self.state_values: dict[str, QLabel] = {}
        for name, color in (("ACTIVE", "#7060d5"), ("PASSIVE", "#a18edb"), ("IDLE", "#8d96aa")):
            column = QHBoxLayout()
            column.setSpacing(4)
            title = label("●  " + name, "eyebrow")
            title.setStyleSheet(f"color:{color};font-size:9px;letter-spacing:0px;")
            column.addWidget(title)
            value = label("0m")
            value.setStyleSheet("font-size:11px;font-weight:600;")
            column.addWidget(value)
            self.state_values[name] = value
            breakdown.addLayout(column)
        layout.addLayout(breakdown)
        self.state_bar = StateBar()
        layout.addWidget(self.state_bar)

        card = QFrame()
        card.setObjectName("card")
        current = QHBoxLayout(card)
        current.setContentsMargins(8, 5, 8, 5)
        current.setSpacing(6)
        self.current_state = label("STARTING", "state")
        self.current_app = label("Getting ready…", "appName")
        self.current_app.setMaximumWidth(150)
        self.current_site = label("", "muted")
        self.current_site.setMaximumWidth(150)
        self.current_site.setAccessibleName("Current site")
        self.current_site.hide()
        identity = QVBoxLayout()
        identity.setSpacing(0)
        identity.addWidget(self.current_app)
        identity.addWidget(self.current_site)
        self.current_duration = label("", "muted")
        self.current_duration.setToolTip("Current continuous foreground duration")
        current.addLayout(identity, 1)
        current.addWidget(self.current_duration)
        current.addWidget(self.current_state)
        layout.addWidget(card)

        ranking = QVBoxLayout()
        ranking.setSpacing(1)
        section = QHBoxLayout()
        section.addWidget(label("Top apps", "section"))
        section.addStretch()
        section.addWidget(label("TODAY", "eyebrow"))
        ranking.addLayout(section)
        self.app_rows: list[tuple[QWidget, QLabel, QLabel, QProgressBar]] = []
        for index in range(5):
            widget = QWidget()
            app_layout = QHBoxLayout(widget)
            app_layout.setContentsMargins(0, 0, 0, 0)
            app_layout.setSpacing(4)
            number = label(f"{index + 1:02}", "muted")
            number.setFixedWidth(18)
            name, usage = label(""), label("")
            usage.setFixedWidth(50)
            usage.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            progress = QProgressBar()
            progress.setRange(0, 1000)
            progress.setTextVisible(False)
            progress.setFixedHeight(3)
            progress.setFixedWidth(46)
            app_layout.addWidget(number)
            app_layout.addWidget(name, 1)
            app_layout.addWidget(progress)
            app_layout.addWidget(usage)
            ranking.addWidget(widget)
            widget.hide()
            self.app_rows.append((widget, name, usage, progress))
        self.empty_label = label("Your day starts here.\nUsage appears as you work.", "muted", wrap=True)
        ranking.addWidget(self.empty_label)
        layout.addLayout(ranking, 1)
        self.dashboard_button = QPushButton("Dashboard  ↗")
        self.dashboard_button.clicked.connect(self.dashboard_requested)

        footer = QHBoxLayout()
        self.status_label = label("●", "muted")
        self.status_label.setToolTip("Starting tracking")
        self.pause_button = self._tool("Pause", "Pause tracking")
        self.settings_button = self._tool("Settings", "Open settings")
        footer.addWidget(self.dashboard_button)
        footer.addStretch()
        footer.addWidget(self.status_label)
        footer.addWidget(self.pause_button)
        footer.addWidget(self.settings_button)
        self.pause_button.clicked.connect(self.pause_requested)
        self.settings_button.clicked.connect(self.settings_requested)
        layout.addLayout(footer)

    def paintEvent(self, event: QEvent) -> None:
        # Explicitly paint the translucent top-level surface on Windows. Relying
        # on stylesheet background propagation can leave the desktop visible.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#dfe2ec"))
        painter.setBrush(QColor("#f5f6fa"))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)

    @staticmethod
    def _tool(text: str, description: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(description)
        button.setAccessibleName(description)
        button.setCheckable(checkable)
        return button

    def set_pinned(self, pinned: bool) -> None:
        self.pinned = pinned
        self.pin_button.blockSignals(True)
        self.pin_button.setChecked(pinned)
        self.pin_button.blockSignals(False)

    def set_topmost(self, enabled: bool) -> None:
        self.top_button.blockSignals(True)
        self.top_button.setChecked(enabled)
        self.top_button.blockSignals(False)
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != enabled:
            visible, position = self.isVisible(), self.pos()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
            self.move(position)
            if visible:
                self.show()

    def update_snapshot(self, snapshot: DashboardSnapshot) -> None:
        summary = snapshot.summary
        self.day_label.setText("TODAY  ·  " + summary.day.strftime("%a, %d %b").upper())
        self.total_label.setText(duration_text(summary.total_ms))
        values = (summary.active_ms, summary.passive_ms, summary.idle_ms)
        for name, value in zip(("ACTIVE", "PASSIVE", "IDLE"), values):
            self.state_values[name].setText(duration_text(value))
        self.state_bar.values = values
        self.state_bar.update()
        self.current_state.setText(snapshot.state.value)
        domain = snapshot.current_domain if snapshot.current_app and not snapshot.excluded and not snapshot.paused else None
        self.current_site.setVisible(bool(domain))
        self.current_site.setText(self.current_site.fontMetrics().elidedText(domain or "", Qt.TextElideMode.ElideRight, 146))
        self.current_site.setToolTip(domain or "")
        if snapshot.current_app:
            name = application_name(snapshot.current_app)
            self.current_app.setText(self.current_app.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 146))
            self.current_app.setToolTip(name)
            self.current_duration.setText(duration_text(snapshot.current_duration_ms, seconds=True))
            self.current_duration.setToolTip("Current stretch · " + duration_text(snapshot.current_duration_ms, seconds=True))
        else:
            self.current_app.setText("Tracking paused" if snapshot.paused else (
                "Excluded from tracking" if snapshot.excluded else "No foreground app"))
            self.current_app.setToolTip("")
            self.current_duration.setText("")
            self.current_duration.setToolTip("Resume whenever you're ready" if snapshot.paused else "This interval does not count toward usage")
        maximum = max((app.duration_ms for app in summary.top_apps), default=1)
        for index, (widget, name_label, usage, progress) in enumerate(self.app_rows):
            visible = index < len(summary.top_apps)
            widget.setVisible(visible)
            if visible:
                app = summary.top_apps[index]
                name = application_name(app.name)
                name_label.setText(name_label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 180))
                name_label.setToolTip(name)
                usage.setText(duration_text(app.duration_ms))
                progress.setValue(round(app.duration_ms / max(1, maximum) * 1000))
        self.empty_label.setVisible(not summary.top_apps)
        self.pause_button.setText("Resume" if snapshot.paused else "Pause")
        self.pause_button.setToolTip("Resume tracking" if snapshot.paused else "Pause tracking")
        self.status_label.setToolTip("Paused" if snapshot.paused else "Tracking locally")
        self.adjustSize()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "pinned"):
            QTimer.singleShot(0, self.hide_if_inactive)
        return super().event(event)

    def hide_if_inactive(self) -> None:
        active = QApplication.activeWindow()
        if self.isVisible() and not self.pinned and active is not self and not (
            active is not None and self.isAncestorOf(active)
        ):
            self._last_auto_hide = time.monotonic()
            self.hide()

    def recently_auto_hidden(self) -> bool:
        return time.monotonic() - self._last_auto_hide < 0.3

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
