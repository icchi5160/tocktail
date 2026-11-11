"""Thread-safe desktop bridge; this QThread never touches widgets."""

from pathlib import Path
from datetime import date
from queue import Empty, Full, Queue
import time
from typing import Callable, Protocol

from PySide6.QtCore import QThread, Signal

from tocktail.config.settings import Settings
from tocktail.browser.bridge import open_bridge
from tocktail.core.models import Observation
from tocktail.runtime.service import TrackingService
from tocktail.storage.instance import exclusive_database
from tocktail.storage.sqlite import Store


class Monitor(Protocol):
    save_titles: bool

    def start(self) -> None: ...
    def sample(self, source: str = "poll") -> Observation: ...
    def wait(self, timeout: float) -> bool: ...
    def close(self) -> None: ...


def windows_monitor() -> Monitor:
    from tocktail.monitors.windows import WindowsMonitor
    return WindowsMonitor()


class TrackingWorker(QThread):
    snapshot_ready = Signal(object)
    settings_ready = Signal(object)
    failed = Signal(str)
    history_ready = Signal(object)
    history_failed = Signal(str)

    def __init__(self, database: Path, monitor_factory: Callable[[], Monitor] = windows_monitor) -> None:
        super().__init__()
        self.database = database
        self.monitor_factory = monitor_factory
        self.commands: Queue[tuple[str, object]] = Queue(maxsize=16)
        self.setObjectName("Tocktail tracking worker")

    def submit(self, command: str, value: object) -> bool:
        try:
            self.commands.put_nowait((command, value))
            return True
        except Full:
            return False

    def run(self) -> None:
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with exclusive_database(self.database), Store(self.database) as store:
                service = TrackingService(store)
                monitor = self.monitor_factory()
                bridge = open_bridge(self.database)
                try:
                    self.settings_ready.emit(service.settings)
                    monitor.start()
                    next_sample = next_publish = 0.0
                    signaled = False
                    while not self.isInterruptionRequested():
                        changed = False
                        while True:
                            try:
                                command, value = self.commands.get_nowait()
                            except Empty:
                                break
                            if command == "settings" and isinstance(value, Settings):
                                service.configure(value)
                                self.settings_ready.emit(value)
                                changed = True
                            elif command == "paused" and isinstance(value, bool):
                                service.paused = value
                                changed = True
                            elif command == "history" and isinstance(value, tuple) and len(value) == 2:
                                if all(isinstance(day, date) for day in value):
                                    try:
                                        self.history_ready.emit(store.history(*value, service.engine.current))
                                    except ValueError as error:
                                        self.history_failed.emit(str(error))
                        now = time.monotonic()
                        if signaled or changed or now >= next_sample:
                            monitor.save_titles = service.settings.save_titles
                            source = "win_event" if signaled else "poll"
                            observations = (monitor.observations(source) if hasattr(monitor, "observations")
                                            else (monitor.sample(source),))
                            for observation in observations:
                                events = ()
                                if bridge is not None:
                                    observation, events = bridge.observe(observation, service.settings)
                                service.observe(observation, events)
                            if now >= next_sample:
                                next_sample = now + 1
                            if changed or now >= next_publish:
                                self.snapshot_ready.emit(service.snapshot())
                                next_publish = now + 1
                        signaled = monitor.wait(min(0.25, max(0, next_sample - time.monotonic())))
                        if signaled:
                            time.sleep(0.02)
                finally:
                    try:
                        if bridge is not None:
                            bridge.close()
                    finally:
                        monitor.close()
                        service.close()
        except Exception as error:
            self.failed.emit(str(error))
