# Phase 1 implementation and verification

This document records Phase 1's scope and results. The project now also includes [Phase 2](PHASE2.md).
Use the README and Phase 2 notes for the current dependency set and total test counts.

Design source: [DESIGN.md](DESIGN.md). Phase 2 and later implementations are out of scope.

## Acceptance check

| Phase 1 criterion | Implementation and evidence |
| --- | --- |
| Detect foreground application | ctypes adapter reads HWND, PID and executable path; real Windows test and CLI recording detected `code.exe` |
| Record application changes as sessions | Pure engine splits app/HWND changes; synthetic stream is persisted transactionally to SQLite |
| Classify ACTIVE / IDLE | Pure classifier uses last-input age, default threshold 300 seconds; native CLI recorded both states in separate runs |
| Store history in SQLite | Five required tables, WAL, schema versioning, checkpoints and separate event/session records |
| Retain history after restart | Same DB reused in two CLI runs; prior ACTIVE interval remained alongside the new IDLE interval |
| Relevant unit tests pass | 53 default tests passed; 2 native tests skipped by default; all 55 passed with `--run-windows` |

## Module boundaries

The monitor produces immutable `Observation` values. `classify` computes a `Target` without OS/GUI/DB
dependencies. `SessionEngine.observe` returns a `Batch` of immutable session snapshots and events.
The storage adapter commits each batch atomically. The CLI owns the only DB-writing connection and
acts as the worker/consumer. The WinEvent callback thread only posts bounded, coalesced notifications.

The core's interval-aggregation helper exists to verify accounting and midnight clipping. A dashboard,
UI summaries and user-facing reporting are not implemented in this phase.

The core accepts verified content and media evidence as values to exercise its accounting contracts.
Only an eventual trusted adapter may set `content_verified`; the current Windows adapter never does.
Browser window IDs are never equated to HWNDs. Browser/HWND correlation, disconnection transport and
Chrome/Edge integration have not been implemented or tested end to end.

## Persistence and recovery

Schema v1 creates `applications`, `contents`, `activity_events`, `sessions` and `settings`, including
indexes and overlap-prevention triggers. Schema v2 adds the session's `is_excluded` flag. Migration
tests start from a real v1 database and verify that existing history survives the v2 migration.
Each migration executes in a transaction and advances `PRAGMA user_version`; newer unknown schemas
are refused. A migration list provides the entry point for future changes.

Open sessions persist an `ended_at` checkpoint while `is_open=1`. Core state transitions close the old
session and open the next in one storage transaction. Unchanged sessions checkpoint every five seconds.
The database rejects negative duration, interval overlap and more than one open session. Empty intervals
do not overlap other intervals. A failed transaction rolls back session and event changes together.

Before recovery the CLI acquires an OS file lock beside the DB. Recovery closes open sessions at their
saved endpoints with `CRASH_RECOVERY`, logging a recovery event. It never fills the offline interval.
The hard-exit test commits a WAL checkpoint in a subprocess, exits via `os._exit` without cleanup and
then verifies recovery and automatic OS lock release. Gaps between saved history and a later startup
remain absent intervals; within-run observation gaps over five seconds get explicit UNTRACKED sessions.

Busy/locked writes retry up to three times with short backoff and a one-second SQLite busy timeout.
Persistent failure stops the CLI and lets a subsequent start recover the last committed state. No
unbounded queue of observations or failed database writes is retained.

## Time and privacy decisions

Each run anchors its timeline in UTC milliseconds and advances it using monotonic elapsed milliseconds.
Clock steps are logged with the observed UTC time. A backward wall clock cannot shorten a session or
overlap saved history; it may temporarily offset the accounting timeline from civil time. Restart uses
at least the last persisted endpoint. Across an observation gap, a later wall-clock value can become
the new anchor while the entire gap is UNTRACKED.

Activity changes take effect at observation time. Normal heartbeat uncertainty is approximately one
second. A delayed worker can increase that uncertainty; a gap over five seconds is entirely excluded
from usage. GetLastInputInfo's 32-bit tick is handled with unsigned wraparound arithmetic.

Events record identity/state changes and minimal idle-age evidence, not every poll or input event.
They are useful for inspection but cannot perfectly reconstruct all activity when observations are
missing. Title text is absent unless `--save-titles` is explicitly supplied; stdout never prints titles.
Excluded intervals carry `is_excluded=1` and no application/content identity, distinguishing deliberate
exclusion from unavailable observations without retaining the excluded app's metadata. The own-PID
rule uses the same exclusion path. The standard `applications.is_excluded` field is reserved for future
persistent configuration; Phase 1 invocation exclusions do not create application rows for excluded apps.

## Verification performed

Verified on 2026-11-06 (Asia/Tokyo), Windows with Python 3.12.14 and uv 0.12.19:

```text
uv run pytest -q
53 passed, 2 skipped

uv run pytest -q --run-windows
55 passed
```

Coverage includes session start/end/checkpoints; application, window and content changes; ACTIVE→IDLE,
ACTIVE→PASSIVE and PASSIVE→IDLE; browser-detail disconnection fallback; confirmed-media requirements;
midnight clipping including UTC boundaries for Japan; backwards/forwards clock steps; stale and duplicate
observations; deterministic randomized streams; pause/lock/suspend classification; conservative sleep-gap
handling; graceful reopen and hard-exit recovery; WAL; migration; single-writer locks; SQLite busy failures;
transaction rollback; excluded data privacy; and database-level interval constraints.

The native tests read actual foreground/input information and deliver a WinEvent through the registered
hook/message pump. Inside the execution sandbox `GetForegroundWindow` returned NULL. Native verification
therefore ran outside that sandbox. The two execution contexts had different temporary-directory
permissions: a normal user run reproduced 40 passed, 2 skipped, 13 setup errors and 2 warnings, all
from access to the shared pytest temporary directory and cache. The root `conftest.py` now supplies
a fresh workspace `.pytest-runs/run-<random>/` temporary and cache directory per invocation. This makes
the documented commands work without manual path overrides or changes to shared Windows ACLs.
The directories remain for diagnostics and are gitignored. Explicit temp/cache overrides are respected;
the default per-run cache does not retain `--last-failed` history between invocations.
This is an execution-environment workaround, not an administrator requirement:

```powershell
uv run pytest -q --run-windows -W error
```

After this fix, the default tests also passed in both the sandbox and normal user execution contexts
with warnings treated as errors: 53 passed, 2 native tests skipped.

CLI smoke verification used `.smoke/phase1.sqlite3`:

```powershell
uv run tocktail --db .smoke/phase1.sqlite3 --idle-seconds 3600 --duration 7
uv run tocktail --db .smoke/phase1.sqlite3 --idle-seconds 1 --duration 2
```

The first recording detected `code.exe` and saved 7,000 ms ACTIVE. The next process reopened the same DB
and saved approximately 2,032 ms IDLE. Both sessions were closed with `SHUTDOWN`; an overlap query returned
zero and `PRAGMA integrity_check` returned `ok`. These short thresholds are smoke-test options only;
the application default remains 300 seconds. Actual desktop focus switching between multiple applications
was not automated; session splitting was verified through synthetic observations and SQLite persistence.

## Deferred environmental validation

- Native Windows power and session-lock notifications remain Phase 4. Pure suspend/lock logic and long
  observation-gap protection are tested; a physical sleep/lock cycle has **not** been tested. Short sleep
  intervals below the gap threshold are not guaranteed to be excluded yet.
- PASSIVE is exercised with synthetic verified-media observations. No real browser playback detection,
  browser transport, extension disconnection or multi-window HWND correlation has been validated.
- No 24-hour soak test, GUI behavior, autostart, installer or packaged executable has been tested.
- Core/storage tests are platform-independent in construction; this session ran them on Windows only.

## 日本語での要点

Phase 1の監視・セッション管理・SQLite保存・CLIを実装しました。通常テストは53件成功、Windows実動試験を含めると55件成功です。
実CLIでACTIVE/IDLEの記録、同じDBでの再起動、履歴保持、重複ゼロ、整合性を確認しました。
クラッシュ復旧と保守的な観測空白除外を先行実装し、除外期間は個別アプリ情報を保存せず識別できます。
物理的なスリープ・ロック試験とWindows通知対応、ブラウザ通信、GUIは今回の対象外です。
使い方は [日本語README](../README.ja.md) を参照してください。
