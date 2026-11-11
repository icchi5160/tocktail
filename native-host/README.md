# Tocktail Native Messaging Host

The importable implementation lives in `desktop/src/tocktail/browser/native_host.py` so the uv-installed
Windows launcher can find it without a shell script or a separate dependency environment.

Run `uv sync --dev` to generate `.venv/Scripts/tocktail-native-host.exe`.
The browser starts that executable directly; stdout is reserved for length-prefixed UTF-8 JSON.
The host validates caller origin, browser ancestry, message schema, sequence and size, then forwards to
the same-user, local-only Windows Named Pipe. It never writes a database or starts the desktop app.

See [browser setup](../docs/BROWSER_SETUP.md) before preparing or registering the host.
