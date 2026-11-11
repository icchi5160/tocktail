"""Opt-in per-user Windows startup. Registry is the source of truth."""

import argparse
from pathlib import Path
import subprocess
import sys

from tocktail.config.paths import default_database


KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Tocktail"


def command(database: Path) -> str:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).with_name("Tocktail.exe")
        args = [str(executable)]
    else:
        executable = Path(sys.executable).with_name("pythonw.exe")
        args = [str(executable), "-m", "tocktail.ui.app"]
    if not executable.is_file():
        raise ValueError("Desktop executable is unavailable")
    return subprocess.list2cmdline([*args, "--db", str(database.resolve())])


def read() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, kind = winreg.QueryValueEx(key, VALUE)
            if kind != winreg.REG_SZ or not isinstance(value, str):
                raise ValueError("Unexpected Tocktail startup registry value")
            return value
    except FileNotFoundError:
        return None


def set_enabled(enabled: bool, database: Path) -> None:
    import winreg
    expected = command(database)
    existing = read()
    # Do not silently replace another installation/custom DB's startup entry.
    if existing is not None and existing != expected:
        raise ValueError("Another Tocktail installation/database owns Windows startup; disable it there first")
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, expected)
    elif existing is not None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage opt-in Windows startup for the current user")
    parser.add_argument("action", choices=("enable", "disable", "status"))
    parser.add_argument("--db", type=Path, default=default_database())
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            print(read() or "Windows startup is disabled")
        else:
            set_enabled(args.action == "enable", args.db)
            print("Windows startup " + ("enabled" if args.action == "enable" else "disabled"))
    except (OSError, ValueError) as error:
        print(f"Startup change failed: {error}", file=sys.stderr)
        return 1
    return 0
