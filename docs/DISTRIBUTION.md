# Windows portable distribution

[日本語](DISTRIBUTION.ja.md)

Target: Windows 11 x64. This is an unsigned portable build, not a signed installer. Python and uv are
not required on the destination PC. Windows may show a reputation prompt for unsigned binaries.

Binary publication is pending a complete bundled-dependency license/notice/source review; see
[third-party software](../THIRD_PARTY_NOTICES.md). The current archive is for local testing.

## Install and launch

1. Extract the entire `Tocktail` folder from `Tocktail-Windows-x64.zip` into a stable, user-writable folder.
   Keep `Tocktail.exe`, `TocktailTools.exe`, `tocktail-native-host.exe` and `_internal` together.
2. Close an existing Tocktail instance, then launch `Tocktail.exe`. Close its popup to keep tracking in the tray.
3. Use **Dashboard** to view 1/7/30-day history. The end date is selectable. The dashboard refreshes every
   five seconds while open; closing it leaves tracking running.
4. Enable **Start with Windows** in the tray menu if wanted. It is off by default and writes only
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Tocktail`. No administrator rights are required.

Activity/settings remain in `%LOCALAPPDATA%\Tocktail\tocktail.sqlite3`. A custom DB can be selected with
`Tocktail.exe --db C:\path\activity.sqlite3`; use the same DB in setup/startup commands.
The packaged app and development version use the same single-writer lock when their DB paths match.

## Chrome / Edge

Load the included `browser-extension` folder as an unpacked extension in Chrome/Edge's extensions page,
with developer mode enabled. Obtain the actual extension IDs. Do not move/delete this folder afterward.
The packaged host is independent of a source checkout or virtual environment.

From PowerShell in the extracted folder (omit an unused browser argument):

```powershell
.\TocktailTools.exe browser-host prepare --chrome-id YOUR_CHROME_ID --edge-id YOUR_EDGE_ID
Get-Content .browser-host\browser-host.json
Get-Content .browser-host\com.tocktail.bridge.json
.\TocktailTools.exe browser-host install --from .browser-host
```

Verify that the manifest points to the neighboring `tocktail-native-host.exe`, then install. Restart
Tocktail and reload the extension. Host registration uses current-user Chrome/Edge NativeMessagingHosts
keys. It replaces this host's registration; it does not install an extension or change browser profiles.
Moving from a source build changes the host path: quit Tocktail and disable/reload the extension to close
the old host before re-registering. Do not overwrite files still running in the old distribution.

```powershell
.\TocktailTools.exe sites
.\TocktailTools.exe startup status
.\TocktailTools.exe startup enable
.\TocktailTools.exe startup disable
```

Multiple browser windows work when process/focus and unique native titles agree. Identical or unavailable
titles fall back to app-only tracking. The popup shows a verified domain beneath the browser name.
URL paths/queries, input contents and private browsing are not collected. Title saving remains opt-in.

## Upgrade / uninstall

Disable **Start with Windows** using the old installation first. Quit Tocktail and disable the extension
to release Native Host executables. Extract the new build into a new folder, then prepare/register its
host with the real IDs and re-enable startup if wanted. Database migrations run on launch; back up the
data folder with Tocktail closed before upgrading. Never copy a live SQLite file without its WAL.

To uninstall, quit Tocktail and disable/reload the extension, then run from this installation:

```powershell
.\TocktailTools.exe startup disable
.\TocktailTools.exe browser-host unregister
```

Remove the browser extension and extracted application folder. Unregister only removes registry entries
that belong to this installation. Activity history, settings and host JSON under LocalAppData are kept.
Delete `%LOCALAPPDATA%\Tocktail` manually only if you explicitly want to erase history and all settings.

## Build from source

Exit Tocktail and disable the extension first: Windows locks running `.exe` launchers and `uv sync` may
otherwise fail during upgrades. In a Windows x64 checkout:

```powershell
uv sync --dev --group build
uv run --group build python scripts/build_windows.py
```

The build-only dependency is PyInstaller; the runtime has no new dependency. Outputs are `dist/Tocktail`
and `dist/Tocktail-Windows-x64.zip`. The distribution uses PyInstaller's shared onedir layout with
separate windowed desktop and console tools/Native Host. It performs no installation or registry changes.
See [PyInstaller spec files](https://pyinstaller.org/en/stable/spec-files.html).

Physical sleep/hibernate, actual logon startup, clean-PC installation, signing and 24-hour real-time soak
need manual release validation; automated notification injection and accelerated testing do not replace them.
