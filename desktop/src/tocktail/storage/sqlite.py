import json
import sqlite3
import time
from collections.abc import Mapping
from datetime import date, timedelta, tzinfo
from pathlib import Path

from tocktail.core.models import ActivityState, Batch, Session
from tocktail.core.summary import AppUsage, DailySummary, day_bounds
from tocktail.core.history import HistoryReport, HistorySession, SiteUsage


MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE applications (
        id INTEGER PRIMARY KEY, process_name TEXT NOT NULL,
        executable_path TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
        is_excluded INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE contents (
        id INTEGER PRIMARY KEY, app_id INTEGER NOT NULL REFERENCES applications(id),
        content_key TEXT NOT NULL, content_type TEXT NOT NULL DEFAULT 'browser',
        title TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT '', url TEXT,
        UNIQUE(app_id, content_key)
    );
    CREATE TABLE activity_events (
        id INTEGER PRIMARY KEY, event_type TEXT NOT NULL, occurred_at INTEGER NOT NULL,
        source TEXT NOT NULL, payload_json TEXT NOT NULL, schema_version INTEGER NOT NULL
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, app_id INTEGER REFERENCES applications(id),
        content_id INTEGER REFERENCES contents(id), hwnd INTEGER,
        activity_state TEXT NOT NULL CHECK(activity_state IN
          ('ACTIVE','PASSIVE','IDLE','PAUSED','LOCKED','SUSPENDED','UNTRACKED')),
        started_at INTEGER NOT NULL, ended_at INTEGER NOT NULL,
        is_open INTEGER NOT NULL CHECK(is_open IN (0,1)), end_reason TEXT,
        CHECK(ended_at >= started_at)
    );
    CREATE UNIQUE INDEX single_open_session ON sessions(is_open) WHERE is_open=1;
    CREATE INDEX session_interval ON sessions(started_at, ended_at);
    CREATE INDEX event_time ON activity_events(occurred_at);
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TRIGGER prevent_overlap_insert BEFORE INSERT ON sessions
    WHEN NEW.ended_at > NEW.started_at AND EXISTS (
        SELECT 1 FROM sessions WHERE id != NEW.id AND ended_at > started_at AND
        ended_at > NEW.started_at AND started_at < NEW.ended_at
    ) BEGIN SELECT RAISE(ABORT, 'overlapping session'); END;
    CREATE TRIGGER prevent_overlap_update BEFORE UPDATE ON sessions
    WHEN NEW.ended_at > NEW.started_at AND EXISTS (
        SELECT 1 FROM sessions WHERE id != NEW.id AND ended_at > started_at AND
        ended_at > NEW.started_at AND started_at < NEW.ended_at
    ) BEGIN SELECT RAISE(ABORT, 'overlapping session'); END;
    """,
    """
    ALTER TABLE sessions ADD COLUMN is_excluded INTEGER NOT NULL DEFAULT 0
      CHECK(is_excluded IN (0,1));
    """,
    """
    CREATE INDEX session_end_interval ON sessions(ended_at, started_at);
    """,
)


class Store:
    """One connection on one thread; each core batch is an atomic transaction."""

    def __init__(self, path: Path | str) -> None:
        self.connection = sqlite3.connect(path, timeout=1)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise RuntimeError("Database was created by a newer Tocktail version")
            for index in range(version, len(MIGRATIONS)):
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n" + MIGRATIONS[index]
                    + f"\nPRAGMA user_version={index + 1};\nCOMMIT;"
                )
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def recover(self) -> int:
        """Never fill the interval between the last checkpoint and restart."""
        with self.connection:
            rows = self.connection.execute("SELECT id, ended_at FROM sessions WHERE is_open=1").fetchall()
            for row in rows:
                self.connection.execute(
                    "UPDATE sessions SET is_open=0, end_reason='CRASH_RECOVERY' WHERE id=?", (row["id"],)
                )
                self.connection.execute(
                    "INSERT INTO activity_events(event_type,occurred_at,source,payload_json,schema_version) "
                    "VALUES ('CRASH_RECOVERY',?,'storage',?,1)",
                    (row["ended_at"], json.dumps({"session_id": row["id"]})),
                )
        return len(rows)

    @property
    def last_end_ms(self) -> int:
        return self.connection.execute("SELECT COALESCE(MAX(ended_at),0) FROM sessions").fetchone()[0]

    def get_setting(self, key: str, default: object = None) -> object:
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: object) -> None:
        self.set_settings({key: value})

    def set_settings(self, values: Mapping[str, object]) -> None:
        with self.connection:
            self.connection.executemany(
                "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(key, json.dumps(value)) for key, value in values.items()],
            )

    def daily_summary(self, day: date, current: Session | None = None, zone: tzinfo | None = None) -> DailySummary:
        """Replace the saved live checkpoint with its latest observed endpoint."""
        start, end = day_bounds(day, zone)
        rows = self.connection.execute(
            "SELECT s.activity_state,a.process_name,a.executable_path,"
            "SUM(MAX(0,MIN(s.ended_at,?)-MAX(s.started_at,?))) AS duration "
            "FROM sessions s LEFT JOIN applications a ON a.id=s.app_id "
            "WHERE s.started_at<? AND s.ended_at>? AND s.id!=? "
            "GROUP BY s.activity_state,a.id",
            (end, start, end, start, current.id if current else ""),
        ).fetchall()
        totals = {state: 0 for state in ActivityState}
        apps: dict[str, AppUsage] = {}

        def add(state: ActivityState, name: str | None, path: str | None, duration: int) -> None:
            totals[state] += duration
            if name and path and state in {ActivityState.ACTIVE, ActivityState.PASSIVE} and duration > 0:
                previous = apps.get(path)
                apps[path] = AppUsage(name, path, duration + (previous.duration_ms if previous else 0))

        for row in rows:
            add(ActivityState(row["activity_state"]), row["process_name"], row["executable_path"], row["duration"])
        if current:
            duration = max(0, min(end, current.ended_at) - max(start, current.started_at))
            app = current.target.application
            add(current.target.state, app.process_name if app else None,
                app.executable_path if app else None, duration)
        ranked = tuple(sorted(apps.values(), key=lambda app: (-app.duration_ms, app.name, app.executable_path))[:5])
        return DailySummary(day, totals[ActivityState.ACTIVE], totals[ActivityState.PASSIVE],
                            totals[ActivityState.IDLE], ranked)

    def history(self, start_day: date, end_day: date, current: Session | None = None,
                zone: tzinfo | None = None) -> HistoryReport:
        if not 0 <= (end_day - start_day).days < 31:
            raise ValueError("Choose a range of 1 to 31 days")
        start, end = day_bounds(start_day, zone)[0], day_bounds(end_day, zone)[1]
        # SQLite does aggregation; raw history is bounded to the newest 500 rows.
        rows = self.connection.execute(
            "SELECT s.activity_state,a.process_name,a.executable_path,c.domain,"
            "SUM(MAX(0,MIN(s.ended_at,?)-MAX(s.started_at,?))) AS duration "
            "FROM sessions s LEFT JOIN applications a ON a.id=s.app_id "
            "LEFT JOIN contents c ON c.id=s.content_id "
            "WHERE s.started_at<? AND s.ended_at>? AND s.id!=? "
            "GROUP BY s.activity_state,a.id,c.domain",
            (end, start, end, start, current.id if current else "")).fetchall()
        values = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        live = None
        if current:
            duration = max(0, min(end, current.ended_at) - max(start, current.started_at))
            app, content = current.target.application, current.target.content
            if duration:
                values.append((current.target.state.value, app.process_name if app else None,
                               app.executable_path if app else None, content.domain if content else None, duration))
                live = HistorySession(max(start, current.started_at), min(end, current.ended_at),
                                      current.target.state.value, app.process_name if app else "",
                                      content.domain if content else "", current.target.excluded)
        states = {state.value: 0 for state in ActivityState}
        apps: dict[str, AppUsage] = {}
        sites: dict[str, int] = {}
        for state, name, path, domain, duration in values:
            states[state] += duration
            if state in {"ACTIVE", "PASSIVE"} and duration:
                if name and path:
                    apps[path] = AppUsage(name, path, duration + (apps[path].duration_ms if path in apps else 0))
                if domain:
                    sites[domain] = sites.get(domain, 0) + duration
        recent = self.connection.execute(
            "SELECT MAX(s.started_at,?),MIN(s.ended_at,?),s.activity_state,"
            "COALESCE(a.process_name,''),COALESCE(c.domain,''),s.is_excluded "
            "FROM sessions s LEFT JOIN applications a ON a.id=s.app_id LEFT JOIN contents c ON c.id=s.content_id "
            "WHERE s.started_at<? AND s.ended_at>? AND s.id!=? "
            "ORDER BY s.started_at DESC,s.id LIMIT 500",
            (start, end, end, start, current.id if current else "")).fetchall()
        sessions = [HistorySession(*r) for r in recent]
        if live:
            sessions.append(live)
        days = []
        day = start_day
        while day <= end_day:
            days.append((day, self.daily_summary(day, current, zone).total_ms))
            day += timedelta(days=1)
        return HistoryReport(start_day, end_day, tuple(states.items()),
                             tuple(sorted(apps.values(), key=lambda a: (-a.duration_ms, a.executable_path))[:100]),
                             tuple(SiteUsage(d, value) for d, value in sorted(sites.items(), key=lambda v: (-v[1], v[0]))[:100]),
                             tuple(days), tuple(sorted(sessions, key=lambda r: -r.started_at)[:500]))

    def save(self, batch: Batch) -> None:
        if not batch.sessions and not batch.events:
            return
        for attempt in range(3):
            try:
                with self.connection:
                    self._save(batch)
                return
            except sqlite3.OperationalError as error:
                if attempt == 2 or not any(word in str(error).lower() for word in ("locked", "busy")):
                    raise
                time.sleep(0.05 * (attempt + 1))

    def _save(self, batch: Batch) -> None:
        for session in batch.sessions:
            target = session.target
            app_id = None
            content_id = None
            if app := target.application:
                self.connection.execute(
                    "INSERT INTO applications(process_name,executable_path,display_name) VALUES (?,?,?) "
                    "ON CONFLICT(executable_path) DO UPDATE SET process_name=excluded.process_name",
                    (app.process_name, app.executable_path, app.process_name),
                )
                app_id = self.connection.execute(
                    "SELECT id FROM applications WHERE executable_path=?", (app.executable_path,)
                ).fetchone()[0]
            if target.content and app_id:
                content = target.content
                self.connection.execute(
                    "INSERT INTO contents(app_id,content_key,title,domain) VALUES (?,?,?,?) "
                    "ON CONFLICT(app_id,content_key) DO UPDATE SET title=excluded.title,domain=excluded.domain",
                    (app_id, content.key, content.title, content.domain),
                )
                content_id = self.connection.execute(
                    "SELECT id FROM contents WHERE app_id=? AND content_key=?", (app_id, content.key)
                ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO sessions(id,app_id,content_id,hwnd,activity_state,started_at,ended_at,is_open,end_reason,is_excluded) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "ended_at=excluded.ended_at,is_open=excluded.is_open,end_reason=excluded.end_reason",
                (session.id, app_id, content_id, target.hwnd, target.state.value,
                 session.started_at, session.ended_at, int(session.is_open), session.end_reason, int(target.excluded)),
            )
        self.connection.executemany(
            "INSERT INTO activity_events(event_type,occurred_at,source,payload_json,schema_version) "
            "VALUES (?,?,?,?,?)",
            [(event.event_type, event.occurred_at, event.source, json.dumps(event.payload),
              event.schema_version) for event in batch.events],
        )
