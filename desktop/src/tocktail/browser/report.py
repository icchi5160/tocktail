import argparse
from datetime import date
from pathlib import Path
import sqlite3

from tocktail.config.paths import default_database
from tocktail.core.summary import day_bounds


def domain_usage(connection: sqlite3.Connection, day: date) -> tuple[tuple[str, int], ...]:
    start, end = day_bounds(day)
    rows = connection.execute(
        "SELECT c.domain,SUM(MAX(0,MIN(s.ended_at,?)-MAX(s.started_at,?))) AS duration "
        "FROM sessions s JOIN contents c ON c.id=s.content_id "
        "WHERE s.activity_state IN ('ACTIVE','PASSIVE') AND s.started_at<? AND s.ended_at>? "
        "AND c.domain!='' GROUP BY c.domain ORDER BY duration DESC,c.domain",
        (end, start, end, start),
    ).fetchall()
    return tuple((row[0], row[1]) for row in rows if row[1] > 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read recorded site usage; never modifies the database")
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("--day", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)
    try:
        with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            rows = domain_usage(connection, args.day)
        print(f"Site usage for {args.day} (ACTIVE + PASSIVE; saved checkpoints)")
        for domain, milliseconds in rows:
            print(f"{milliseconds / 1000:10.1f}s  {domain}")
        if not rows:
            print("No verified site usage recorded.")
    except sqlite3.Error:
        print("Could not read site usage. Check the database path and schema.")
        return 1
    return 0
