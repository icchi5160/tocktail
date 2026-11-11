from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 12px; color: #202337; }
QWidget#popup { background: #f5f6fa; border: 1px solid #dfe2ec; border-radius: 16px; }
QDialog { background: #f5f6fa; }
QLabel { background: transparent; border: none; }
QLabel#brand { font-size: 15px; font-weight: 700; }
QLabel#eyebrow { color: #737b91; font-size: 10px; font-weight: 600; letter-spacing: 1px; }
QLabel#total { font-size: 38px; font-weight: 600; color: #252640; }
QLabel#muted { color: #737b91; font-size: 11px; }
QLabel#section { font-size: 12px; font-weight: 600; }
QLabel#appName { font-size: 14px; font-weight: 600; }
QLabel#state { color: #6154cc; font-size: 10px; font-weight: 700; }
QLabel#error { color: #b23d4c; }
QFrame#card { background: #ffffff; border: 1px solid #e8eaf2; border-radius: 12px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px; color: #6c7389; }
QToolButton:hover { background: #e9eaf3; color: #30334c; }
QToolButton:checked { background: #e7e3fb; color: #6654c8; border-color: #d7cef5; }
QPushButton { background: white; border: 1px solid #dfe2ec; border-radius: 7px; padding: 8px 12px; }
QPushButton:hover { background: #efedf9; border-color: #bfb4e9; }
QPushButton:disabled { color: #959aad; }
QPushButton#primary { background: #7060d5; color: white; border: 0; font-weight: 600; }
QPushButton#primary:hover { background: #6250c6; }
QProgressBar { border: none; background: #f0f1f7; height: 3px; border-radius: 1px; }
QProgressBar::chunk { background: #a89ce8; border-radius: 1px; }
QSpinBox, QPlainTextEdit { background: white; border: 1px solid #dfe2ec; border-radius: 6px; padding: 7px; }
QCheckBox { spacing: 8px; }
QMenu { background: #ffffff; border: 1px solid #dfe2ec; padding: 5px; }
QMenu::item { padding: 8px 24px 8px 12px; border-radius: 5px; }
QMenu::item:selected { background: #efedf9; color: #5143aa; }
QTabWidget::pane { border: 1px solid #dfe2ec; background: white; border-radius: 6px; }
QTabBar::tab { background: #eceef5; padding: 8px 16px; border: none; color: #737b91; }
QTabBar::tab:selected { background: white; color: #6154cc; }
QTableWidget { background: white; alternate-background-color: #f8f9fc; border: none; selection-background-color: #e7e3fb; selection-color: #202337; }
QHeaderView::section { background: #eceef5; color: #737b91; border: none; padding: 7px; }
"""


COMPACT_POPUP_STYLE = """
QLabel { font-size: 11px; }
QLabel#brand { font-size: 13px; }
QLabel#total { font-size: 28px; }
QLabel#eyebrow { font-size: 9px; letter-spacing: 0px; }
QLabel#muted { font-size: 10px; }
QLabel#section { font-size: 11px; }
QLabel#appName { font-size: 12px; }
QLabel#state { font-size: 9px; }
QToolButton { font-size: 11px; padding: 2px 4px; }
QPushButton { font-size: 11px; padding: 3px 7px; }
"""


def tray_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 64, size / 64)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#7764da"))
        painter.drawRoundedRect(QRectF(2, 2, 60, 60), 18, 18)
        painter.setPen(QPen(QColor("white"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(QRectF(15, 15, 34, 34))
        painter.drawLine(QPointF(32, 23), QPointF(32, 33))
        painter.drawLine(QPointF(32, 33), QPointF(40, 37))
        painter.end()
        icon.addPixmap(pixmap)
    return icon
