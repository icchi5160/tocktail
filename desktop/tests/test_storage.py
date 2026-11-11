from dataclasses import replace
import sqlite3
import subprocess
import sys

import pytest

from tocktail.core.engine import SessionEngine
from tocktail.core.models import Batch, Content
from tocktail.storage.instance import exclusive_database
from tocktail.storage.sqlite import MIGRATIONS, Store


def test_tables_migration_and_wal(tmp_path):
    path = tmp_path / "tracker.sqlite3"
    with Store(path) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"applications", "contents", "sessions", "activity_events", "settings"} <= tables
        store.set_setting("idle_seconds", 30)
    with Store(path) as store:
        assert store.get_setting("idle_seconds") == 30
        assert store.get_setting("missing", False) is False


def test_history_and_crash_recovery_at_checkpoint(tmp_path, observation):
    path = tmp_path / "tracker.sqlite3"
    engine = SessionEngine()
    with Store(path) as store:
        store.save(engine.observe(observation()))
        for second in range(1, 8):
            store.save(engine.observe(observation(second)))
    with Store(path) as store:
        assert store.recover() == 1
        session = store.connection.execute("SELECT * FROM sessions").fetchone()
        assert session["ended_at"] == observation(5).utc_ms
        assert session["end_reason"] == "CRASH_RECOVERY"
        assert session["is_open"] == 0
        assert store.recover() == 0
        restarted = SessionEngine(initial_floor_ms=store.last_end_ms)
        store.save(restarted.observe(observation(60)))
        rows = store.connection.execute("SELECT * FROM sessions ORDER BY started_at").fetchall()
        assert len(rows) == 2
        assert rows[0]["ended_at"] < rows[1]["started_at"]


def test_transition_batch_is_atomic_on_failure(tmp_path, observation):
    engine = SessionEngine()
    with Store(tmp_path / "db") as store:
        first = engine.observe(observation())
        store.save(first)
        batch = engine.observe(observation(1, hwnd=99))
        bad = replace(batch.sessions[1], ended_at=batch.sessions[1].started_at - 1)
        with pytest.raises(sqlite3.IntegrityError):
            store.save(Batch((batch.sessions[0], bad), batch.events))
        rows = store.connection.execute("SELECT * FROM sessions").fetchall()
        assert len(rows) == 1 and rows[0]["is_open"] == 1
        store.save(batch)
        assert store.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


def test_database_rejects_overlap_and_second_open(tmp_path, observation):
    engine = SessionEngine()
    first = engine.observe(observation()).sessions[0]
    with Store(tmp_path / "db") as store:
        closed = replace(first, is_open=False, ended_at=first.started_at + 2000)
        store.save(Batch((closed,)))
        with pytest.raises(sqlite3.IntegrityError, match="overlapping session"):
            store.save(Batch((replace(closed, id="other"),)))
        opened = replace(first, id="open", started_at=closed.ended_at, ended_at=closed.ended_at)
        store.save(Batch((opened,)))
        with pytest.raises(sqlite3.IntegrityError):
            store.save(Batch((replace(opened, id="second"),)))


def test_content_persistence(tmp_path, observation):
    engine = SessionEngine()
    batch = engine.observe(observation(content=Content("tab", domain="example.com"),
                                       browser_connected=True, content_verified=True))
    with Store(tmp_path / "db") as store:
        store.save(batch)
        assert store.connection.execute("SELECT domain FROM contents").fetchone()[0] == "example.com"
        assert store.connection.execute("SELECT content_id FROM sessions").fetchone()[0] is not None


def test_exclusion_does_not_persist_identity_or_title(tmp_path, observation):
    engine = SessionEngine()
    with Store(tmp_path / "db") as store:
        store.save(engine.observe(observation(excluded=True, title="sensitive")))
        assert store.connection.execute("SELECT count(*) FROM applications").fetchone()[0] == 0
        assert "editor" not in store.connection.execute("SELECT payload_json FROM activity_events").fetchone()[0]
        assert "sensitive" not in repr(store.connection.execute("SELECT * FROM activity_events").fetchall())
        assert store.connection.execute("SELECT is_excluded FROM sessions").fetchone()[0] == 1


def test_upgrade_v1_preserves_existing_history(tmp_path, observation):
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(MIGRATIONS[0])
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO sessions VALUES ('old',NULL,NULL,NULL,'UNTRACKED',1,2,0,'SHUTDOWN')"
        )
    with Store(path) as store:
        row = store.connection.execute("SELECT * FROM sessions").fetchone()
        assert row["id"] == "old"
        assert row["is_excluded"] == 0
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_single_writer_lock_released(tmp_path):
    path = tmp_path / "db"
    with exclusive_database(path):
        with pytest.raises(RuntimeError, match="Another Tocktail"):
            with exclusive_database(path):
                pytest.fail("second writer acquired the lock")
    with exclusive_database(path):
        pass


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)


def test_busy_database_retries_without_partial_writes(tmp_path, observation):
    path = tmp_path / "db"
    with Store(path) as store, sqlite3.connect(path) as blocker:
        store.connection.execute("PRAGMA busy_timeout=1")
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.save(SessionEngine().observe(observation()))
        assert store.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_many_transitions_remain_nonoverlapping_in_sqlite(tmp_path, observation):
    engine = SessionEngine()
    with Store(tmp_path / "db") as store:
        for second in range(30):
            store.save(engine.observe(observation(second, hwnd=second % 3)))
        store.save(engine.stop())
        assert store.connection.execute("SELECT count(*) FROM sessions WHERE is_open=1").fetchone()[0] == 0
        assert store.connection.execute(
            "SELECT count(*) FROM sessions a JOIN sessions b ON a.id < b.id "
            "AND a.started_at < b.ended_at AND b.started_at < a.ended_at"
        ).fetchone()[0] == 0


def test_empty_session_is_not_an_overlap(tmp_path, observation):
    session = SessionEngine().observe(observation()).sessions[0]
    with Store(tmp_path / "db") as store:
        empty = replace(session, id="empty", is_open=False,
                        started_at=session.started_at + 1000, ended_at=session.started_at + 1000)
        store.save(Batch((empty,)))
        store.save(Batch((replace(session, ended_at=session.started_at + 2000, is_open=False),)))


def test_hard_process_exit_recovers_wal_and_releases_lock(tmp_path):
    path = tmp_path / "crash.sqlite3"
    child = """
import os
from pathlib import Path
import sys
from tocktail.core.engine import SessionEngine
from tocktail.core.models import Observation, Application
from tocktail.storage.sqlite import Store
from tocktail.storage.instance import exclusive_database
path = Path(sys.argv[1])
with exclusive_database(path):
    store = Store(path)
    engine = SessionEngine()
    for t in [0, 5000, 6000]:
        store.save(engine.observe(Observation(1800000000000+t, t, Application('app.exe','c:/app.exe'), 1)))
    os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", child, str(path)], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with exclusive_database(path), Store(path) as store:
        assert store.recover() == 1
        row = store.connection.execute("SELECT * FROM sessions").fetchone()
        assert row["ended_at"] - row["started_at"] == 5000
        assert row["end_reason"] == "CRASH_RECOVERY"
