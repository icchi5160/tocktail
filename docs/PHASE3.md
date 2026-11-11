# Phase 3 — Browser integration

Historical phase report. Current stability/dashboard/distribution work: [Phase 4](PHASE4.md).

[Setup (English)](BROWSER_SETUP.md) · [導入手順（日本語）](BROWSER_SETUP.ja.md)

Source of truth: [DESIGN.md](DESIGN.md). The 336 × 310px desktop popup remains compact. Phase 4's detailed
dashboard, autostart, native lock/power notifications and distribution remain out of scope.

## Delivered components

| Component | Implementation |
| --- | --- |
| Common Chrome/Edge extension | MV3 module worker; active tab/origin/title, focus, normal-window count, media content scripts; private browsing forbidden |
| Native Messaging Host | uv-installed Windows `.exe` launcher; bounded little-endian framing, caller-origin and browser ancestry validation; no GUI/DB imports |
| Named Pipe | ctypes overlapped I/O; eight connections; per-slot coalesced mailbox; current-user protected DACL; reject remote clients; first-instance protection |
| Correlation | Separate process/window evidence adapter and OS-independent matching policy; authenticated browser root, focus and unique exact native title required |
| Accounting | Verified domain/content values enter existing session engine; tab/navigation/media/connection events; expired/disconnected details fall back to app-only |
| Privacy | Origin-only transport, no paths/query/fragment/credentials; title persistence off by default; unverified browser titles suppressed; verified domain/subdomain exclusions |
| Site report | `tocktail-sites` read-only daily ACTIVE + PASSIVE totals grouped by domain |
| Setup | Reviewable preparation, explicit HKCU registration, scoped unregister, rollback; no automatic browser/profile installation |

## Correlation feasibility and its limit

Chrome documents that `--parent-window` is **0 for service-worker calls**. A browser `window.id` is an
extension-local identifier, not a native HWND. Therefore the current MV3 connection cannot provide a
direct per-window HWND binding. This implementation does not invent one. [Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging).

The implemented, deliberately narrow policy requires all of the following:

1. The OS-reported pipe peer has Chrome/Edge process ancestry. Consecutive Chromium utility ancestors are
   resolved to the browser root; the native host independently checks this and the caller origin.
2. The configured extension origin is allowed for that browser family. Chrome and Edge ID lists are separate.
3. The OS foreground executable and PID match that browser root, and foreground HWND stays unchanged
   across evidence acquisition.
4. The extension reports focus and at least one normal window. Native enumeration includes visible and
   minimized windows across the browser family/profiles. The reported count cannot exceed the native
   windows belonging to the authenticated root. Incomplete or changing inventories are rejected.
5. The foreground native title exactly equals the active page title plus the standard browser suffix.
   With multiple native windows, every title must be available and exactly one may match, including
   windows in other profiles. Titles are rechecked during acquisition and are transient unless saving
   is enabled. Title alone is insufficient: authenticated root PID and foreground/focus must also agree.
   Profile/decorated titles that do not match are rejected.
6. Exactly one connection satisfies the policy, with fresh increasing snapshots. Two observations at least
   250ms apart must corroborate the same candidate; typical warm-up is one monitor heartbeat.

Multiple windows with distinct native titles are supported. Identical or unavailable titles, conflicting
connections and inconsistent inventories fall back to application-only tracking. This extends the original
singleton policy without changing DESIGN.md: site details are still withheld whenever correspondence is
uncertain. It does not create a permanent HWND binding or compare unrelated browser and Windows IDs.
No extension permission, protocol or database schema change is needed; restart the desktop to apply.

## Data path and boundaries

```text
HTTP(S) HTML audio/video → content.js
  → MV3 background.js (current document-ID check, active tab only)
  → Native Messaging stdin/stdout
  → tocktail-native-host.exe → current-user Named Pipe
  → BrowserBridge + WindowsEvidence → TrackingService
  → pure SessionEngine → atomic SQLite event/session batch
```

The extension enumerates window inventory without populating all tabs, then queries only the active tab
of the focused normal window. `webNavigation.getAllFrames` is used only to compare current frame document
IDs with media-message document IDs. It does not subscribe to or persist a navigation history stream.
`tabs`, `nativeMessaging`, `alarms`, `webNavigation` and HTTP(S) content-script access serve those specific
functions. Native ports and a 30-second reconnect alarm support MV3 lifecycle behavior. [Chrome worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle).

Full browser URLs may exist transiently in Chrome API return values. `siteOrigin` removes credentials,
path, query and fragment before any Native Messaging transfer. The host repeats validation and rejects
messages containing those URL components. SQLite `contents.url` stays NULL. Origin/URL-path persistence
was not added because URL storage is optional in the design and stripping potentially secret paths/queries
is the conservative privacy choice. Domains and independent opaque navigation tokens provide site/content
session splitting without reversible URL-derived keys.

Title opt-in affects new records. Content keys distinguish recorded-title versions, so turning title
storage off does not overwrite old content metadata or attach its title to a future untitled record.
Browser titles cannot reach the desktop event log before site verification. The service independently
enforces title and domain-exclusion policy even if an adapter supplies extra data.

Excluded domains include subdomains using label boundaries: `example.com` matches `a.example.com`, not
`notexample.com`. Verified exclusions contain no application/content identity. During unverified intervals
the app cannot know which domain is excluded, so only generic application time continues, with browser
titles suppressed. Exclusion does not retrospectively erase earlier history.

Browser snapshots are limited to 16KiB, validate exact fields/types, finite integer ranges, UTF-8, JSON key
uniqueness, origin syntax, version, sequence and message age. Invalid/replayed connections cannot reacquire
content until reconnecting. Metadata expires after at most four seconds including source/transport age;
media evidence expires after 2.5 seconds. Disconnect notifications clear that connection on the next sample.
The bridge is loaded for the configured DB at desktop startup; the original console monitor is app-only.

The pipe uses explicit user SID security rather than Windows' permissive default descriptor. Its actual
kernel DACL is read back in native tests. It rejects remote clients, protects the first instance and keeps
only one latest update per each of eight slots. Writes have a 500ms bound and no historical event backlog
is retained. This is a same-account trust boundary, not a defense against arbitrary code already running
as the same user. [Windows Named Pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights).

The native host lives inside the Python package so uv can install a real Windows executable launcher.
`native-host/README.md` explains the layout. Preparation writes files only; installation explicitly copies
them to LocalAppData and sets HKCU browser host keys, consistent with the documented Chrome/Edge mechanism.
No live browser registration was performed during implementation. [Edge Native Messaging registration](https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging).

## Verification results

Windows, Python 3.12.14, uv 0.12.19, PySide6 Essentials 6.12.0, Node 24.17.0:

```text
uv run pytest -q -W error
175 passed, 7 skipped

uv run pytest -q --run-windows -W error
182 passed

node --test browser-extension/tests/*.test.mjs
12 passed
```

Native tests cover actual pipe send/receive/disconnect, concurrent clients, first-instance protection,
actual DACL readback, a framing→real pipe→bridge→SQLite integration and the previous Windows monitor/UI
tests. The transport integration deliberately simulates browser ancestry/window proof; it is not a loaded
Chrome/Edge extension test. An earlier full run had no accessible foreground HWND and skipped two existing
foreground tests; the final run had foreground access and all 182 passed without skips or warnings.

Pure tests cover Chrome and Edge family allowlists, IDs distinct from HWND, mismatched process/foreground/
title, multiple windows, private/restricted/null pages, incomplete enumeration, duplicate connections,
replay invalidation, reconnection, snapshot/media/transit expiry, domain exclusions and title opt-out,
tab/navigation events across fallback gaps, frame fragmentation, invalid Unicode, size/schema failures,
host registration origin/executable tampering, rollback and scoped unregister. JS tests exercise origin redaction, focused-tab
selection, actual-element media state, hidden/stalled/seeking/ended states, current document IDs, worker
navigation/reconnect behavior and absence of raw URL secrets in transferred messages.

Preparation was also executed against a review directory with placeholder IDs, confirming the installed
launcher path and generated manifest. No real registry keys were changed; registry mutations are tested
using a fake registry interface. The new settings screen was rendered to [phase3-settings.png](assets/phase3-settings.png)
with sample configuration. The popup remains 336 × 310; settings are now 440 × 639 logical pixels.

## Explicitly unverified / remaining work

- The user confirmed successful single-window Chrome recording after installing the extension. Live
  multi-window validation of this update and real Edge validation remain outstanding.
- Real browser playback, iframe/special-player behavior, branded/profile-specific window titles and host
  process ancestry under installed browser versions need manual browser validation. Conservative fallback
  remains active when these proofs are unavailable.
- Multi-window attribution requires unique readable native titles; identical titles and decorated/PWA
  titles may still fall back. Automated tests cover switching, duplicate titles and non-overlap; live
  multi-window Chrome/Edge verification after this update remains a manual check.
- Full URL/path saving is not implemented. Private browsing and internal pages are not tracked in detail;
  IPv6-literal origins also fall back because this version's domain validator handles DNS/IPv4 names.
- Site reporting is a read-only CLI. The detailed dashboard, installers/distribution, autostart, native
  power/lock integration and long-duration soak tests remain Phase 4.

## 日本語での要点

Phase 3の共通MV3拡張、Native Messaging Host、同一ユーザー限定Named Pipe、サイト記録、確認できたメディアによるPASSIVE、サイト除外を実装しました。
通常Pythonテスト175件成功・7件スキップ、最終のWindows実動試験込みで182件成功、JavaScriptテスト12件成功です。
親HWNDが0になるMV3の制約に対し、プロセス・フォーカスに加えて前面タイトルの一意性を検証し、複数ウィンドウでもサイトを割り当てます。
同名タイトルや不確実な情報はアプリ単位へ戻します。URLはoriginまでに縮約し、パスや検索語は送らず、DBのURL欄はNULLです。
Chromeの単一ウィンドウでの記録成功はユーザーが確認済みです。今回の複数ウィンドウ対応とEdgeの実ブラウザ検証は未実施です。

## Current site in the popup

The immutable desktop snapshot now carries the verified current domain from the core target. The compact
popup displays it under the app name, elides long domains and shows the complete domain in a tooltip.
It clears the field on fallback, exclusion, lock or pause. Page titles and raw URLs are not used for this
label, and the existing duration continues to mean continuous foreground app time. No schema or extension
change is required. See [popup preview](assets/phase3-popup.png); dimensions remain 336 × 310px.
