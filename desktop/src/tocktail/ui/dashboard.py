from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tocktail.core.history import HistoryReport
from tocktail.ui.formatting import application_name, duration_text
from tocktail.ui.popup import label


class DayChart(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.days: tuple[tuple[date, int], ...] = ()
        self.setMinimumHeight(100)
        self.setAccessibleName("Daily active and passive usage")

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self.days:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        peak = max(1, *(value for _, value in self.days))
        step = self.width() / len(self.days)
        for index, (_, value) in enumerate(self.days):
            height = (self.height() - 22) * value / peak
            p.setBrush(QColor("#a89ce8"))
            p.drawRoundedRect(QRectF(index * step + 3, self.height() - 22 - height,
                                    max(1, step - 6), height), 3, 3)
        p.setPen(QColor("#737b91"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                   self.days[0][0].strftime("%b %d"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
                   self.days[-1][0].strftime("%b %d"))


class Dashboard(QDialog):
    refresh_requested = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Tocktail · History")
        self.resize(820, 650)
        self.setMinimumSize(640, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(label("Your time, in detail", "brand"))
        header.addStretch()
        self.period = QComboBox()
        self.period.addItems(["1 day", "7 days", "30 days"])
        self.period.setCurrentIndex(1)
        self.end_date = QDateEdit(QDate.currentDate())
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_date.setMaximumDate(QDate.currentDate())
        self.end_date.setAccessibleName("History end date")
        header.addWidget(self.period)
        header.addWidget(self.end_date)
        refresh = QPushButton("Refresh")
        header.addWidget(refresh)
        layout.addLayout(header)
        self.total = label("Loading history…", "total")
        layout.addWidget(self.total)
        self.breakdown = label("", "muted", wrap=True)
        layout.addWidget(self.breakdown)
        self.chart = DayChart()
        layout.addWidget(self.chart)
        tabs = QTabWidget()
        self.apps = self._table(["Application", "Usage", "Share"])
        self.sites = self._table(["Site", "Usage", "Share of total"])
        self.sessions = self._table(["Start", "End", "State", "Application", "Site"])
        for widget, name in ((self.apps, "Apps"), (self.sites, "Sites"), (self.sessions, "Sessions")):
            tabs.addTab(widget, name)
        layout.addWidget(tabs, 1)
        self.status = label("", "muted", wrap=True)
        layout.addWidget(self.status)
        layout.addWidget(label("Sites are part of app usage. App and site totals are not added together.\n"
                               "Top 100 apps/sites · Latest 500 sessions · Times use your local timezone.", "muted", wrap=True))
        refresh.clicked.connect(self.refresh)
        self.period.currentIndexChanged.connect(self.refresh)
        self.end_date.dateChanged.connect(self.refresh)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        return table

    def interval(self) -> tuple[date, date]:
        end = self.end_date.date().toPython()
        return end - timedelta(days=(1, 7, 30)[self.period.currentIndex()] - 1), end

    def refresh(self) -> None:
        self.status.setText("Updating…")
        self.refresh_requested.emit(*self.interval())

    @staticmethod
    def _rows(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                table.setItem(index, column, item)

    def update_report(self, report: HistoryReport) -> None:
        if (report.start, report.end) != self.interval():
            return  # Ignore a response for a previous selection.
        self.total.setText(duration_text(report.total_ms))
        self.breakdown.setText("   ·   ".join(f"{state} {duration_text(value)}" for state, value in report.states if value))
        self.chart.days = report.days
        self.chart.setToolTip("\n".join(f"{day}: {duration_text(value)}" for day, value in report.days))
        self.chart.update()
        share = lambda value: f"{100 * value / max(1, report.total_ms):.1f}%"
        self._rows(self.apps, [[application_name(a.name), duration_text(a.duration_ms), share(a.duration_ms)] for a in report.applications])
        self._rows(self.sites, [[s.domain, duration_text(s.duration_ms), share(s.duration_ms)] for s in report.sites])
        local = lambda value: datetime.fromtimestamp(value / 1000).strftime("%m-%d %H:%M:%S")
        self._rows(self.sessions, [[local(s.started_at), local(s.ended_at), s.state,
                                    "Excluded" if s.excluded else application_name(s.app) if s.app else "—", s.domain]
                                   for s in report.sessions])
        self.status.setText("Updated just now" if report.sessions else "No recorded activity in this period")
