"""Accelerated 24-hour accounting simulation. This is NOT a real-time Windows soak."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from tocktail.core.engine import SessionEngine
from tocktail.core.models import Application, Batch, Observation
from tocktail.storage.sqlite import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="new test database (must not already exist)")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if args.db.exists() or not 1 <= args.hours <= 168:
        parser.error("Use a new database and 1..168 simulated hours")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    store = Store(args.db)
    engine = SessionEngine()
    anchor = int(datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp() * 1000)
    sessions, events = [], []
    recoveries = 0
    try:
        for second in range(args.hours * 3600 + 1):
            application = Application("editor.exe" if (second // 60) % 2 else "chrome.exe", "c:/test.exe")
            observation = Observation(anchor + second * 1000, second * 1000, application, 10, 20,
                                      idle_ms=400000 if second % 600 > 500 else 0,
                                      locked=second % 3600 >= 3300,
                                      suspended=3000 <= second % 3600 < 3100)
            batch = engine.observe(observation)
            sessions.extend(batch.sessions)
            events.extend(batch.events)
            if len(sessions) >= 512 or second % 21600 == 0:
                store.save(Batch(tuple(sessions), tuple(events)))
                sessions.clear()
                events.clear()
            if second and second % 21600 == 0 and second < args.hours * 3600:
                # Simulated abrupt termination after durable checkpoints.
                store.close()
                store = Store(args.db)
                recoveries += store.recover()
                engine = SessionEngine(initial_floor_ms=store.last_end_ms)
        batch = engine.stop()
        store.save(Batch(tuple(sessions) + batch.sessions, tuple(events) + batch.events))
        c = store.connection
        overlap = c.execute("SELECT COUNT(*) FROM (SELECT started_at,LAG(ended_at) OVER (ORDER BY started_at,ended_at) AS prior "
                            "FROM sessions WHERE ended_at>started_at) WHERE started_at<prior").fetchone()[0]
        negative = c.execute("SELECT COUNT(*) FROM sessions WHERE ended_at<started_at").fetchone()[0]
        active = c.execute("SELECT COALESCE(SUM(ended_at-started_at),0) FROM sessions WHERE activity_state IN ('ACTIVE','PASSIVE')").fetchone()[0]
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        assert overlap == negative == 0 and integrity == "ok"
        assert active < args.hours * 3600000
        print(json.dumps({"mode": "accelerated simulation, not real-time soak", "simulated_hours": args.hours,
                          "elapsed_seconds": round(time.monotonic()-started, 2), "crash_recoveries": recoveries,
                          "sessions": c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                          "overlaps": overlap, "negative": negative, "integrity": integrity}))
    finally:
        store.close()


if __name__ == "__main__":
    main()
