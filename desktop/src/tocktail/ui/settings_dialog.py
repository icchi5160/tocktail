from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget,
)

from tocktail.config.settings import Settings
from tocktail.ui.popup import label


class SettingsDialog(QDialog):
    settings_requested = Signal(object)

    def __init__(self, settings: Settings, database_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tocktail settings")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(15)
        layout.addWidget(label("Make it yours", "brand"))
        layout.addWidget(label("Changes apply immediately after saving.", "muted"))
        form = QFormLayout()
        form.setVerticalSpacing(14)
        self.idle_seconds = QSpinBox()
        self.idle_seconds.setRange(1, 86400)
        self.idle_seconds.setSuffix(" seconds")
        self.idle_seconds.setValue(round(settings.idle_seconds))
        self.idle_seconds.setAccessibleName("Idle threshold in seconds")
        form.addRow("Idle threshold", self.idle_seconds)
        self.pinned = QCheckBox("Keep popup open when clicking elsewhere")
        self.pinned.setChecked(settings.pinned)
        form.addRow("Pin popup", self.pinned)
        self.topmost = QCheckBox("Show popup above other windows")
        self.topmost.setChecked(settings.always_on_top)
        form.addRow("Always on top", self.topmost)
        layout.addLayout(form)
        layout.addWidget(label("Excluded apps", "section"))
        self.excluded_apps = QPlainTextEdit("\n".join(settings.excluded_apps))
        self.excluded_apps.setPlaceholderText("passwordmanager.exe\nOne executable name per line")
        self.excluded_apps.setAccessibleName("Excluded executable names")
        self.excluded_apps.setFixedHeight(90)
        layout.addWidget(self.excluded_apps)
        layout.addWidget(label("Excluded sites (including subdomains)", "section"))
        self.excluded_domains = QPlainTextEdit("\n".join(settings.excluded_domains))
        self.excluded_domains.setPlaceholderText("example.com\nOne domain per line")
        self.excluded_domains.setAccessibleName("Excluded domains")
        self.excluded_domains.setFixedHeight(65)
        layout.addWidget(self.excluded_domains)
        self.save_titles = QCheckBox("Save window titles")
        self.save_titles.setChecked(settings.save_titles)
        layout.addWidget(self.save_titles)
        layout.addWidget(label("Off by default. Titles may contain personal information.\n"
                               "Changing this affects future records; existing history is kept.", "muted", wrap=True))
        layout.addWidget(label("Stored only on this PC", "section"))
        path = label(database_path, "muted", wrap=True)
        path.setMaximumWidth(390)
        layout.addWidget(path)
        self.error_label = label("", "error", wrap=True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        self.buttons.accepted.connect(self.request_save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def request_save(self) -> None:
        try:
            settings = Settings(self.idle_seconds.value(), self.save_titles.isChecked(),
                                tuple(self.excluded_apps.toPlainText().splitlines()),
                                self.pinned.isChecked(), self.topmost.isChecked(),
                                tuple(self.excluded_domains.toPlainText().splitlines()))
        except ValueError as error:
            self.show_error(str(error))
            return
        self.error_label.hide()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.settings_requested.emit(settings)

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.show()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
