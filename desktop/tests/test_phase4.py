from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import sqlite3
import sys

import pytest

from tocktail.config import startup
from tocktail.core.engine import SessionEngine
from tocktail.core.models import ActivityState, Application, Content
from tocktail.runtime.service import TrackingService
from tocktail.storage.sqlite import Store


@pytest.mark.parametrize("inactive", ["locked", "suspended"])
@pytest.mark.parametrize("duration", [2, 3600])
def test_native_inactive_interval_is_never_usage(tmp_path, observation, inactive, duration):
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        service.observe(observation())
        service.observe(observation(1, **{inactive: True}))
        service.observe(observation(1 + duration))
        service.observe(observation(2 + duration))
        service.close()
        day = datetime.fromtimestamp(observation().utc_ms / 1000).date()
        report = store.history(day, day)
        assert report.total_ms == 2000
        assert dict(report.states)[inactive.upper()] == duration * 1000
        events = {r[0] for r in store.connection.execute("SELECT event_type FROM activity_events")}
        expected = {"SESSION_LOCKED", "SESSION_UNLOCKED"} if inactive == "locked" else {"SYSTEM_SUSPENDED", "SYSTEM_RESUMED"}
        assert expected <= events


def test_suspend_wall_elapsed_without_monotonic_elapsed_is_not_active(observation):
    engine = SessionEngine()
    engine.observe(observation())
    engine.observe(observation(1, suspended=True))
    batch = engine.observe(observation(2, utc_ms=observation(3601).utc_ms))
    assert any(s.target.state == ActivityState.SUSPENDED and s.ended_at-s.started_at == 3600000
               for s in batch.sessions)
    assert engine.current.started_at == observation(3601).utc_ms


def test_history_live_checkpoint_replacement_midnight_and_domain_hierarchy(tmp_path, observation):
    midnight = int(datetime(2026, 11, 7, tzinfo=timezone.utc).timestamp() * 1000)
    with Store(tmp_path / "db") as store:
        service = TrackingService(store, own_pid=999)
        # One second on each side, then a live second after the saved checkpoint.
        for second in range(4):
            service.observe(observation(second, utc_ms=midnight - 1000 + second * 1000,
                application=Application("chrome.exe", "c:/chrome.exe"),
                content=Content("site", "", "example.com"), browser_connected=True, content_verified=True))
        current = service.engine.current
        report = store.history(date(2026, 11, 6), date(2026, 11, 7), current, timezone.utc)
        assert report.total_ms == 3000
        assert [value for _, value in report.days] == [1000, 2000]
        assert sum(a.duration_ms for a in report.applications) == 3000
        assert sum(s.duration_ms for s in report.sites) == 3000
        assert len(report.sessions) == 1
        assert sum(s.ended_at-s.started_at for s in report.sessions) == 3000
        assert store.history(date(2026, 11, 7), date(2026, 11, 7), current, timezone.utc).total_ms == 2000


def test_history_bounds_and_limits(tmp_path, observation):
    day = datetime.fromtimestamp(observation().utc_ms / 1000).date()
    with Store(tmp_path / "db") as store:
        assert store.history(day, day).total_ms == 0
        for end in (day - timedelta(days=1), day + timedelta(days=31)):
            with pytest.raises(ValueError):
                store.history(day, end)
        service = TrackingService(store, own_pid=999)
        for second in range(602):
            service.observe(observation(second, hwnd=second + 1))
        service.close()
        report = store.history(day, day)
        assert report.total_ms == 601000
        assert len(report.sessions) == 500


def test_crash_recovery_after_suspend_never_backfills(tmp_path, observation):
    path = tmp_path / "db"
    with Store(path) as store:
        service = TrackingService(store, own_pid=999)
        service.observe(observation())
        service.observe(observation(1, suspended=True))
        # Abrupt end: intentionally omit service.close().
    with Store(path) as store:
        service = TrackingService(store, own_pid=999)
        service.observe(observation(3600))
        service.observe(observation(3601))
        service.close()
        assert store.connection.execute("SELECT COUNT(*) FROM sessions WHERE end_reason='CRASH_RECOVERY'").fetchone()[0] == 1
        day = datetime.fromtimestamp(observation().utc_ms / 1000).date()
        assert store.history(day, day).total_ms == 2000


def test_startup_quotes_paths_and_preserves_another_installation(tmp_path, monkeypatch):
    executable = tmp_path / "space here" / "python.exe"
    executable.parent.mkdir()
    executable.with_name("pythonw.exe").touch()
    monkeypatch.setattr(sys, "executable", str(executable))
    database = tmp_path / "data space" / "activity.sqlite3"
    value = startup.command(database)
    assert '"' + str(executable.with_name("pythonw.exe")) + '"' in value
    assert '"' + str(database.resolve()) + '"' in value
    class Registry:
        HKEY_CURRENT_USER = 1
        REG_SZ = 1
    monkeypatch.setitem(sys.modules, "winreg", Registry())
    monkeypatch.setattr(startup, "read", lambda: "another installation")
    with pytest.raises(ValueError, match="Another Tocktail"):
        startup.set_enabled(True, database)
    with pytest.raises(ValueError, match="Another Tocktail"):
        startup.set_enabled(False, database)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows notification window")
def test_real_notification_window_receives_synthetic_lock_and_power_events():
    import ctypes
    from ctypes import wintypes as W
    from tocktail.monitors.windows import WindowsMonitor
    monitor = WindowsMonitor()
    monitor.start()
    try:
        assert monitor.lifecycle.hwnd
        send = monitor.user32.SendMessageW
        send.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        send.restype = ctypes.c_ssize_t
        monitor.observations()
        send(monitor.lifecycle.hwnd, 0x02B1, 7, 0)
        send(monitor.lifecycle.hwnd, 0x0218, 4, 0)
        send(monitor.lifecycle.hwnd, 0x0218, 18, 0)
        # Unlock re-queries the real session; never locks/unlocks the computer.
        send(monitor.lifecycle.hwnd, 0x02B1, 8, 0)
        samples = monitor.observations()
        assert samples[0].locked and not samples[0].suspended
        assert samples[1].locked and samples[1].suspended
        assert samples[2].locked and not samples[2].suspended
        assert [s.monotonic_ms for s in samples] == sorted(s.monotonic_ms for s in samples)
    finally:
        monitor.close()
    assert monitor.lifecycle.hwnd is None
    assert not monitor._thread.is_alive()


def test_abrupt_subprocess_exit_preserves_only_durable_checkpoint(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    path = tmp_path / "crash.sqlite3"
    script = """
import os, sys
from pathlib import Path
from tocktail.storage.sqlite import Store
from tocktail.storage.instance import exclusive_database
from tocktail.runtime.service import TrackingService
from tocktail.core.models import Observation,Application
p=Path(sys.argv[1])
with exclusive_database(p), Store(p) as store:
    service=TrackingService(store,own_pid=-1)
    for second in range(7):
        service.observe(Observation(1800000000000+second*1000,second*1000,Application('editor.exe','c:/editor.exe'),10,20))
    os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], timeout=15)
    assert result.returncode == 9
    with Store(path) as store:
        assert store.recover() == 1
        row = store.connection.execute("SELECT started_at,ended_at,is_open,end_reason FROM sessions").fetchone()
        assert tuple(row) == (1800000000000, 1800000005000, 0, "CRASH_RECOVERY")
        assert store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows per-user registry")
def test_startup_registry_roundtrip_in_isolated_test_key(tmp_path, monkeypatch):
    import winreg
    from uuid import uuid4
    # Never writes the real Windows Run key or enables actual logon startup.
    key = "Software\\TocktailTests\\" + uuid4().hex
    monkeypatch.setattr(startup, "KEY", key)
    database = tmp_path / "data space.sqlite3"
    try:
        assert startup.read() is None
        startup.set_enabled(True, database)
        assert startup.read() == startup.command(database)
        startup.set_enabled(True, database)
        startup.set_enabled(False, database)
        assert startup.read() is None
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
