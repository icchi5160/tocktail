"""Build a portable Windows distribution; does not install or register anything."""

from pathlib import Path
import argparse
import hashlib
import shutil
import subprocess
import sys


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build on Windows; cross-compilation is not supported")
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assemble-only", action="store_true", help="refresh documentation/archive from an existing built folder")
    args = parser.parse_args()
    if not args.assemble_only:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        str(root / "distribution" / "Tocktail.spec")], cwd=root, check=True)
    target = root / "dist" / "Tocktail"
    if not all((target / name).is_file() for name in ("Tocktail.exe", "TocktailTools.exe", "tocktail-native-host.exe")):
        raise SystemExit("Build the three executables before assembling the archive")
    for name in ("README.md", "README.ja.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(root / name, target / name)
    shutil.copytree(root / "browser-extension", target / "browser-extension",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests", "node_modules"))
    shutil.copytree(root / "docs", target / "docs", dirs_exist_ok=True)
    for name in ("DISTRIBUTION.md", "DISTRIBUTION.ja.md"):
        shutil.copy2(root / "docs" / name, target / name)
    archive = shutil.make_archive(str(root / "dist" / "Tocktail-Windows-x64"), "zip", root / "dist", "Tocktail")
    archive_path = Path(archive)
    archive_path.with_suffix(".zip.sha256").write_text(
        hashlib.sha256(archive_path.read_bytes()).hexdigest() + "  " + archive_path.name + "\n",
        encoding="ascii")
    print(archive)


if __name__ == "__main__":
    main()
