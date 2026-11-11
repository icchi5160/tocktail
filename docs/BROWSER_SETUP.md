# Chrome / Edge setup

[日本語](BROWSER_SETUP.ja.md) · [Phase 3 implementation and limitations](PHASE3.md)

The desktop app records browser details after you load the companion extension and register its Native
Messaging Host. Setup is per Windows user and needs no administrator privileges. These instructions
change browser/Windows configuration only when you explicitly perform them; repository preparation and
automated tests do not install extensions or register a real browser host.

## 1. Install the Python environment and load the extension

```powershell
uv sync --dev
```

Open `chrome://extensions` in Chrome or `edge://extensions` in Edge. Enable Developer mode and choose
**Load unpacked**, selecting this repository's `browser-extension` directory. The same directory works
for both browsers. Use current Chrome/Edge (Chromium 120+). Copy each browser's extension ID from its
extension card or the companion's options page. Each ID contains 32 letters from `a` through `p`.
The two IDs can be identical for an unpacked directory; configure both browser entries anyway.

Private/Incognito/InPrivate tracking is disabled by the manifest. The extension requests `tabs` for the
focused tab, `nativeMessaging` for the local host, `alarms` for reconnects, `webNavigation` to validate
the currently loaded media-frame document IDs, and HTTP(S) host access for its media content script.
It does not inspect forms, input fields, page text, keystrokes or mouse events.

## 2. Prepare and review host files

Quit any running Tocktail desktop instance before registration. Replace the placeholders below with
your actual IDs; omit a browser's flag if you do not use that browser.

```powershell
uv run tocktail-browser-host prepare `
  --chrome-id YOUR_CHROME_EXTENSION_ID `
  --edge-id YOUR_EDGE_EXTENSION_ID

Get-Content .browser-host\browser-host.json
Get-Content .browser-host\com.tocktail.bridge.json
```

`prepare` only writes reviewable JSON files inside `.browser-host`; it makes no registry changes.
The manifest's executable should be this environment's `.venv\Scripts\tocktail-native-host.exe`.
Allowed origins must contain only the extension IDs you supplied. There is no wildcard origin.

For a custom database, add `--db C:\path\to\your.sqlite3` to `prepare` and use that same path when
launching the desktop. Each registered configuration targets one database; other development databases
continue application-only monitoring.

## 3. Register for the current Windows user

After reviewing the prepared files:

```powershell
uv run tocktail-browser-host install --from .browser-host
uv run tocktail-desktop
```

`install` copies configuration to `%LOCALAPPDATA%\Tocktail\browser-host.json` and the host manifest to
`%LOCALAPPDATA%\Tocktail\native-host\com.tocktail.bridge.json`. It sets the default manifest path at:

- `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.tocktail.bridge` for configured Chrome IDs.
- `HKCU\Software\Microsoft\Edge\NativeMessagingHosts\com.tocktail.bridge` for configured Edge IDs.

It uses HKCU only and does not touch browser profiles, auto-start settings or machine-wide registry keys.
The project/venv must remain at the prepared path. If moved or recreated, run `uv sync`, prepare and
register again. Registry/file changes are rolled back on setup failure where access permissions allow.

Reload the extension or wait up to approximately 30 seconds for its reconnect alarm. Click its toolbar
action to open its connection page. **Connected to your desktop app** means the local transport is
available; it does not bypass the foreground verification rules below. The app loads bridge configuration
at startup, so restart it after registration changes.

## 4. Verify recording

Multiple browser windows are supported. The foreground page title must identify exactly one native
window across that browser family, with matching process and focus. Identical titles in another window
(including minimized windows or another profile) and unreadable titles force application-only fallback.
Focus an ordinary HTTP(S) page with a nonempty title for at least a few seconds. The native title must
match the active tab title with the standard ` - Google Chrome` or ` - Microsoft Edge` suffix.
Wait at least five seconds for a checkpoint, then inspect saved domain totals:

```powershell
uv run tocktail-sites
uv run tocktail-sites --day 2026-11-06
# Custom database:
uv run tocktail-sites --db C:\path\to\your.sqlite3
```

The report is read-only and sums ACTIVE + PASSIVE by domain for the local day. It uses saved checkpoints,
so current progress can lag by up to five seconds. Site totals are a subdivision of application usage;
they are not added to it again. The original `uv run tocktail` console monitor remains application-only.

Browser site details are assigned only when the focused extension snapshot, native foreground HWND,
browser process ancestry and a unique native title agree over successive observations. The extension
window count must not exceed the native windows belonging to its authenticated browser root.
Chrome's MV3 service worker supplies `--parent-window=0`, so neither that value nor a browser `window.id`
is used as an HWND. Duplicate/unknown titles, stale data, mismatched titles, unknown process ancestry, duplicate
connections or incomplete enumeration deliberately continue application-only recording.
See [Chrome's Native Messaging specification](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)
for the service-worker parent-window constraint.

After the idle threshold, a foreground page can become PASSIVE only if the content script reports a
playing HTML audio/video element, the frame's document ID still belongs to the active tab and the
evidence is no older than 2.5 seconds including transport delay. `tab.audible` is never treated as proof.
Internal browser pages, inaccessible frames, buffering/seeking/ended media and special players may not
provide evidence. Media playback is a usage estimate, not proof that someone is watching.

## Privacy and exclusions

- Extension messages contain only the URL origin (scheme/host/port), never credentials, paths, queries
  or fragments. SQLite stores the normalized domain; `contents.url` remains NULL. Full URL/path recording
  is intentionally not implemented in this phase, although the design allows an optional URL feature.
- Page/native titles are used transiently to check correlation. Saving titles is off by default; enable
  **Save window titles** in desktop Settings to store verified page titles. Unverified browser titles
  stay out of the desktop event log even when title saving is enabled.
- Add `example.com` under **Excluded sites** in desktop Settings to exclude it and its subdomains.
  Verified excluded intervals retain no site/title/application identity. When site correlation is uncertain,
  application-only time continues without guessing a site or its exclusion; browser titles are suppressed.
- These settings affect future desktop observations, not historical records. The CLI has separate invocation
  options and does not apply browser-site exclusions.
- Browser snapshots expire after at most four seconds; disconnect notifications invalidate the matching
  connection on the next worker sample. Missing media evidence can never extend PASSIVE indefinitely.
- The Named Pipe rejects remote clients and its protected DACL grants access only to the current user SID.
  It does not attempt to isolate mutually hostile processes already running as that same Windows user.

## Disconnect and unregister

Closing/reloading the extension or browser invalidates its site/media information. The desktop continues
ordinary application tracking. If the desktop is stopped, the host reports an unavailable connection and
tries forwarding again on later extension heartbeats; no activity backlog is retained.

To unregister, quit Tocktail first, then:

```powershell
uv run tocktail-browser-host unregister
```

Reload/disable the extension or restart the browser to close already-running native hosts. Unregister
removes only HKCU entries that still point at this installation's manifest. Configuration, prepared files,
manifests and all activity history remain on disk. The command does not close an already-running browser
connection or delete any activity database.

## Validation status

Automated tests exercise the JS worker/content scripts with browser API mocks, Python framing/validation,
real Windows Named Pipes and their actual DACL, and a transport-to-SQLite integration using simulated
browser identity/window evidence. **A loaded extension in actual Chrome/Edge has not been tested in this
development session.** Register your real extension IDs and use the manual checks above to validate your
browser/profile/title configuration. Keep fallback behavior enabled whenever those checks cannot prove
the association.

Platform references: [Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging),
[Edge Native Messaging](https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging),
[MV3 worker lifetime](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle),
[Windows pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights).
