"""Phase 1 console runner; Ctrl+C checkpoints the final observation and exits."""

import argparse
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
import time

from tocktail.config.paths import default_database
from tocktail.core.engine import SessionEngine
from tocktail.core.models import Batch, Event
from tocktail.storage.instance import exclusive_database
from tocktail.storage.sqlite import Store


def positive_seconds(value: str) -> float:
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Tocktail Windows activity monitor")
    result.add_argument("--gui", action="store_true", help="launch the desktop tray application")
    result.add_argument("--db", type=Path, default=default_database())
    result.add_argument("--idle-seconds", type=positive_seconds, help="default: stored value or 300")
    result.add_argument("--duration", type=positive_seconds, help="stop after this many seconds")
    result.add_argument("--save-titles", action="store_true", help="opt in to sensitive window titles")
    result.add_argument("--exclude-app", action="append", default=[], metavar="EXE",
                        help="exclude a process name, e.g. passwordmanager.exe (repeatable)")
    return result


def _run(args: argparse.Namespace, store: Store) -> None:
    from tocktail.monitors.windows import WindowsMonitor

    recovered = store.recover()
    threshold = args.idle_seconds if args.idle_seconds is not None else store.get_setting("idle_seconds", 300)
    threshold_ms = int(float(threshold) * 1000)
    if threshold_ms < 1:
        raise ValueError("idle threshold must be at least one millisecond")
    store.set_setting("idle_seconds", threshold_ms / 1000)
    # Privacy options are explicit on every invocation; absent flags are conservative.
    excluded = {name.casefold() for name in args.exclude_app}
    engine = SessionEngine(idle_threshold_ms=threshold_ms, initial_floor_ms=store.last_end_ms)
    monitor = WindowsMonitor(save_titles=args.save_titles)
    print(f"Database: {args.db.resolve()} | recovered: {recovered} | idle: {threshold_ms / 1000:g}s", flush=True)
    print("Tracking; Ctrl+C to stop. Titles and URLs are absent from console output.", flush=True)
    started = time.monotonic()
    last_output: tuple[object, ...] | None = None
    last_title = ""
    storage_healthy = True
    try:
        monitor.start()
        source = "startup"
        next_poll = time.monotonic() + 1
        while True:
            for observation in monitor.observations(source):
                if observation.application and observation.application.process_name.casefold() in excluded:
                    observation = replace(observation, excluded=True, title="")
                batch = engine.observe(observation)
                if args.save_titles and not observation.excluded and observation.application:
                    if observation.title != last_title:
                        batch = Batch(batch.sessions, batch.events + (Event(
                            "WINDOW_TITLE_OBSERVED", observation.utc_ms, observation.source,
                            {"title": observation.title, "hwnd": observation.hwnd},
                        ),))
                last_title = observation.title
                try:
                    store.save(batch)
                except sqlite3.Error:
                    storage_healthy = False
                    raise
                current = engine.current
                assert current is not None
                key = (current.target,)
                if key != last_output:
                    app = current.target.application
                    print(f"{current.target.state.value:10} {app.process_name if app else '(untracked)'}", flush=True)
                    last_output = key
            remaining = None if args.duration is None else args.duration - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                break
            # A fixed heartbeat prevents a busy stream of hooks from delaying polls.
            timeout = min(1, max(0, next_poll - time.monotonic()))
            if remaining is not None:
                timeout = min(timeout, remaining)
            signaled = monitor.wait(timeout)
            source = "win_event" if signaled else "poll"
            if time.monotonic() >= next_poll:
                next_poll = time.monotonic() + 1
            if signaled:
                time.sleep(0.02)  # Coalesce event bursts without an unbounded queue.
    except KeyboardInterrupt:
        pass
    finally:
        monitor.close()
        if storage_healthy:
            store.save(engine.stop())


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.gui:
        from tocktail.ui.app import main as desktop_main
        desktop_args = ["--db", str(args.db)]
        if args.duration is not None:
            desktop_args.extend(["--duration", str(args.duration)])
        if args.idle_seconds is not None or args.save_titles or args.exclude_app:
            parser().error("With --gui, change tracking preferences in Settings instead.")
        return desktop_main(desktop_args)
    if sys.platform != "win32":
        print("Live monitoring requires Windows 11. Pure core tests run on other OSes.", file=sys.stderr)
        return 2
    try:
        args.db = args.db.expanduser().resolve()
        args.db.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_database(args.db), Store(args.db) as store:
            _run(args, store)
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"Tocktail stopped: {error}", file=sys.stderr)
        return 1
    return 0
