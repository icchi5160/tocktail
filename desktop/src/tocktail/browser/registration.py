"""Prepare reviewable host files; registration is an explicit per-user command."""

import argparse
import json
from pathlib import Path
import sys
from typing import Protocol

from tocktail.browser.config import BridgeConfig, config_path
from tocktail.browser.protocol import HOST_NAME
from tocktail.config.paths import default_database


REGISTRY_PATHS = {
    "chrome": "Software\\Google\\Chrome\\NativeMessagingHosts\\" + HOST_NAME,
    "edge": "Software\\Microsoft\\Edge\\NativeMessagingHosts\\" + HOST_NAME,
}


class Registry(Protocol):
    def read(self, family: str) -> str | None: ...
    def write(self, family: str, path: str | None) -> None: ...


def host_manifest(config: BridgeConfig, executable: Path) -> dict[str, object]:
    executable = executable.resolve()
    if not executable.is_file() or executable.suffix.casefold() != ".exe":
        raise ValueError("Run uv sync first: the native host .exe launcher must exist")
    return {"name": HOST_NAME, "description": "Tocktail local browser bridge", "path": str(executable),
            "type": "stdio", "allowed_origins": sorted({f"chrome-extension://{value}/"
                for value in config.chrome_ids + config.edge_ids})}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare_bundle(directory: Path, config: BridgeConfig, executable: Path) -> None:
    manifest = host_manifest(config, executable)
    write_json(directory / "browser-host.json", config.to_dict())
    write_json(directory / f"{HOST_NAME}.json", manifest)


class UserRegistry:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Native host registration requires Windows")
        import winreg
        self.registry = winreg

    def read(self, family: str) -> str | None:
        r = self.registry
        try:
            with r.OpenKey(r.HKEY_CURRENT_USER, REGISTRY_PATHS[family]) as key:
                value, kind = r.QueryValueEx(key, "")
                if kind != r.REG_SZ or not isinstance(value, str):
                    raise ValueError("Existing native host registry entry has an unexpected format")
                return value
        except FileNotFoundError:
            return None

    def write(self, family: str, path: str | None) -> None:
        r = self.registry
        if path is None:
            try:
                r.DeleteKey(r.HKEY_CURRENT_USER, REGISTRY_PATHS[family])
            except FileNotFoundError:
                pass
        else:
            with r.CreateKey(r.HKEY_CURRENT_USER, REGISTRY_PATHS[family]) as key:
                r.SetValueEx(key, "", 0, r.REG_SZ, path)


def install_bundle(directory: Path, registry: Registry, *, destination: Path | None = None,
                   expected_executable: Path | None = None) -> tuple[Path, ...]:
    config = BridgeConfig.load(directory / "browser-host.json")
    raw_manifest = json.loads((directory / f"{HOST_NAME}.json").read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, dict) or not isinstance(raw_manifest.get("path"), str):
        raise ValueError("Invalid prepared native host manifest")
    executable = expected_executable or Path(sys.executable).with_name("tocktail-native-host.exe")
    if raw_manifest != host_manifest(config, executable):
        raise ValueError("Prepared manifest does not match its configuration")
    destination = destination or config_path().parent
    target_config = destination / "browser-host.json"
    target_manifest = destination / "native-host" / f"{HOST_NAME}.json"
    families = [family for family, ids in (("chrome", config.chrome_ids), ("edge", config.edge_ids)) if ids]
    old_values = {family: registry.read(family) for family in families}
    old_files = {path: path.read_bytes() if path.exists() else None for path in (target_config, target_manifest)}
    try:
        write_json(target_config, config.to_dict())
        write_json(target_manifest, raw_manifest)
        for family in families:
            registry.write(family, str(target_manifest.resolve()))
    except BaseException as original_error:
        rollback_failed = False
        for family, value in old_values.items():
            try:
                registry.write(family, value)
            except OSError:
                rollback_failed = True
        for path, data in old_files.items():
            try:
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
            except OSError:
                rollback_failed = True
        if rollback_failed:
            raise RuntimeError("Browser registration failed and rollback was incomplete; inspect the prepared registration paths") from original_error
        raise
    return target_config, target_manifest


def unregister(registry: Registry, *, destination: Path | None = None) -> None:
    target = ((destination or config_path().parent) / "native-host" / f"{HOST_NAME}.json").resolve()
    for family in REGISTRY_PATHS:
        value = registry.read(family)
        if value is not None and Path(value).resolve() == target:
            registry.write(family, None)
    # Keep configuration, manifests and all activity history for inspection/re-registration.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare/register the Tocktail Native Messaging Host")
    actions = parser.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare", help="create reviewable files; makes no registry changes")
    prepare.add_argument("--chrome-id", action="append", default=[])
    prepare.add_argument("--edge-id", action="append", default=[])
    prepare.add_argument("--db", type=Path, default=default_database())
    prepare.add_argument("--output", type=Path, default=Path(".browser-host"))
    install = actions.add_parser("install", help="register prepared files for the current Windows user")
    install.add_argument("--from", dest="directory", type=Path, default=Path(".browser-host"))
    actions.add_parser("unregister", help="remove only this installation's per-user registrations; keep data")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            config = BridgeConfig(args.db.expanduser().resolve(), tuple(args.chrome_id), tuple(args.edge_id))
            prepare_bundle(args.output, config, Path(sys.executable).with_name("tocktail-native-host.exe"))
            print(f"Prepared files in {args.output.resolve()}. Review before running install.")
        elif args.action == "install":
            for path in install_bundle(args.directory, UserRegistry()):
                print(f"Installed {path}")
        else:
            unregister(UserRegistry())
            print("Removed matching Tocktail browser registrations; history and files were kept.")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Browser setup failed: {error}", file=sys.stderr)
        return 1
    return 0
