# Phase 2 — Desktop UI

This document records Phase 2. See [Phase 3](PHASE3.md) for the current browser integration, settings
screen additions, dependencies and total test counts.

Source of truth: [DESIGN.md](DESIGN.md). Phase 3 browser integration and Phase 4 distribution/stability
features are not implemented by this phase.

## Acceptance check

| Requirement | Implementation and verification |
| --- | --- |
| Tray resident application | `QSystemTrayIcon`; left-click toggles the popup, right-click exposes open/pause/settings/quit; native lifecycle test verifies tray visibility |
| Compact popup | 336 × 310px, rounded background, today total, three state totals, current app/stretch, top five apps; sample-data image rendered and visually inspected |
| Pinning | External deactivation hides an unpinned popup; pinned popup stays open; Qt activation tests verify both cases |
| Independent always-on-top | Separate `WindowStaysOnTopHint` flag and persisted preference; tested independently of Pin |
| Hide without stopping | ×, Escape and tray toggling hide the popup without interrupting the worker; controller test checks this separation |
| Header dragging | Header tracks the temporary mouse offset and moves the popup; moved positions stay fixed for that run |
| Today's usage | SQL interval clipping to local midnights; ACTIVE + PASSIVE only; IDLE displayed separately; top-five ranking combines sessions per executable path |
| Live progress | Current in-memory interval replaces its saved checkpoint in summaries; tests prove no double-counting |
| Pause/resume | Commands reach the worker through a bounded queue, split sessions and log TRACKING_PAUSED/TRACKING_RESUMED; paused time is excluded |
| Basic settings | Idle seconds, excluded executable names, future title recording, Pin and Top; validated values save atomically and apply live |
| Own-window exclusion | Service independently checks its own PID, including popup/settings; tests assert excluded intervals contain no application/title identity |
| Safe GUI/worker boundary | Qt queued signals carry frozen settings/snapshots to QObject slots; DB connection and engine stay on one worker thread |
| Dashboard entry | Button opens a coming-soon notice; the actual detailed history dashboard remains Phase 4 |

## Run

```powershell
uv sync --dev
uv run tocktail-desktop
uv run tocktail-desktop --show
```

`uv run tocktail --gui` is another tray entry point. The original `uv run tocktail` console monitor
remains available. Use separate database paths if running development instances simultaneously;
the same-database lock is deliberately shared by the CLI and desktop.

The tray's **Quit Tocktail** action requests interruption, leaves the Qt event loop alive while the
worker stops, unhooks the monitor and closes the last observed session, then exits. Qt window close
events hide the popup rather than stopping the application. Ctrl+C also requests graceful desktop
shutdown when launched from a console. No running QThread is force-terminated.

VS Code includes a **Tocktail: Desktop UI** module launch configuration.

## Implementation

- `config/settings.py`: frozen, validated preferences. The desktop persists them in `settings.desktop`
  and atomically updates the shared `idle_seconds` key for CLI compatibility.
- `core/summary.py`: frozen application/day/snapshot values plus local calendar-day boundary calculation.
  No Qt/Win32/database dependencies were added to the core.
- `storage/sqlite.py`: daily aggregate query, optional live-session replacement and multi-setting transactions.
  Existing schema v2 is sufficient; this phase does not require another database migration.
- `runtime/service.py`: normalizes observations, enforces own/configured exclusions, applies pause/title
  policies, runs the existing engine, persists effects and produces summaries. The app's current stretch
  continues across state changes while the same HWND/application remains foreground. A gap or exclusion
  resets that stretch.
- `ui/worker.py`: owns SQLite and monitoring in `QThread.run`. A maximum of 16 commands queue from the UI.
  The worker checks commands at least every 250ms while idle, supplements hooks with one-second observations
  and emits summary updates approximately once per second. It never manipulates widgets.
- `ui/controller.py`: routes tray/popup actions, waits for durable settings acknowledgement, rejects stale
  preference/pause acknowledgements, positions the popup on the tray's screen and coordinates shutdown.
- `ui/popup.py`, `ui/settings_dialog.py`, `ui/theme.py`: presentation and interaction. The translucent
  top-level popup explicitly paints an opaque rounded surface to prevent desktop bleed-through on Windows.
  The brand/tray icon is drawn with QPainter, so it does not depend on special font glyphs or bitmap assets.

Pinning and always-on-top are independent saved booleans. A saved pinned popup reopens on startup.
Header position is retained for the current run; durable screen-position preferences are not introduced.
Pause is a temporary runtime choice and resets to tracking on restart. Exclusion and title preferences
affect future records only; changing them does not delete earlier history.

PySide6 **Essentials** is the direct GUI dependency. It supplies the required `PySide6.QtCore`, `QtGui`,
`QtWidgets` and `QtTest` modules without installing unused WebEngine/add-ons. `shiboken6` is its required
transitive binding dependency. No GUI library is imported by `core` or `storage`.

SQLite failures stop the worker, display an error and disable tracking/settings controls. Errors do not
manufacture live usage. The error popup stays visible until explicitly hidden; the saved Pin preference
is not changed by that temporary behavior. Restart continues to use Phase 1 crash recovery.

## Verification

Verified on Windows, Python 3.12.14, uv 0.12.19, PySide6 Essentials 6.12.0:

```text
uv run pytest -q -W error
92 passed, 3 skipped

uv run pytest -q --run-windows -W error
95 passed
```

Default tests run without live Windows monitoring. Qt widgets use the offscreen platform; Windows
Segoe UI fonts are explicitly loaded for accurate headless text metrics. The native tests use a separate
process with the real Windows Qt platform, so offscreen settings cannot accidentally validate the native
test. The native subprocess briefly opens the tray, pinned popup and settings dialog, verifies the opaque
popup background, then checks zero open sessions, zero interval overlaps and `integrity_check=ok` after exit.

Phase 2 tests cover settings validation/roundtrip, immediate threshold changes, persistent preferences,
future-only title policy, own/configured exclusions, pause accounting and event logging, current-stretch
continuity, live checkpoint replacement, local-day clipping, ranking, empty-day data, independent window
flags, outside activation, visible labels, background-thread ownership, bounded command queues,
same-database rejection, durable settings acknowledgements and stale UI notification handling.

A separate 60-second real desktop smoke run also reported tray/popup/settings visibility and a running
worker without errors; clean shutdown yielded zero open/overlapping sessions and an intact database.
Computer Use inspected the native settings screen and exercised its always-on-top/save controls using
a test-only database. The initial native visual inspection exposed transparent background bleed-through;
explicit surface painting fixed it, and the native regression test verifies opaque pixels.

Visual previews contain **sample data**, never actual activity:

```powershell
uv run python scripts/preview_ui.py --output docs/assets/phase2-popup.png
uv run python scripts/preview_ui.py --settings --output docs/assets/phase2-settings.png
```

The popup was compacted from 336 × 620 to 336 × 310 logical pixels at the user's request, retaining
all summary fields and five ranked apps. The compact render was visually inspected and all 16 UI tests passed.
Settings remain 440 × 528. Preview code runs no
monitor, opens no database and makes no network requests. See [popup](assets/phase2-popup.png) and
[settings](assets/phase2-settings.png).

For a manual native smoke run with a newly generated test database:

```powershell
uv run python scripts/smoke_desktop.py --duration 8
```

Native tests/smoke runs require an interactive Windows desktop. Within Codex, access to that desktop
requires execution outside the filesystem/desktop sandbox; it does not require Windows administrator
privileges. Default tests retain the per-invocation workspace temp/cache isolation added after Phase 1.

## Remaining limits

- PASSIVE displays historical/synthetic confirmed-media data correctly, but real media detection is still
  unavailable until Phase 3. No browser extension, Native Messaging or HWND/browser correlation is added.
- The detailed dashboard button is an explicit placeholder notice, respecting Phase 4's scope.
- Physical sleep/lock notifications, short-suspend guarantees, autostart, packaging and 24-hour soak testing
  remain Phase 4. Observation-gap safeguards from Phase 1 still apply.
- Basic Qt interaction and native lifecycle are tested. Dragging across mixed-DPI monitors, every taskbar
  placement/overflow configuration, and full manual focus/z-order scenarios have not been exhaustively tested.
- The UI uses English labels; both English and Japanese project documentation are provided.

## 日本語での要点

Phase 2のトレイUI、ポップアップ、Pin/Topの独立制御、時間集計、一時停止・再開、基本設定を実装しました。
監視とDBは専用スレッドが所有し、UIへはQtのSignal/Slotで表示用データだけを渡します。
通常テスト92件成功・3件スキップ、Windows実動試験込みで95件成功です。
実UIの起動と正常終了を確認し、二重加算・未終了セッション・重複がないことを検証しています。
詳細ダッシュボードは案内のみでPhase 4、ブラウザ連携はPhase 3のままです。
