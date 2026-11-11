# Build on Windows: python -m PyInstaller --noconfirm distribution/Tocktail.spec
from pathlib import Path

root = Path(SPECPATH).parent
executables = []
binaries = []
datas = []
for script, name, console in (
    ("desktop_entry.py", "Tocktail", False),
    ("host_entry.py", "tocktail-native-host", True),
    ("tools_entry.py", "TocktailTools", True),
):
    analysis = Analysis([str(root / "distribution" / script)],
                        pathex=[str(root / "desktop" / "src")],
                        excludes=["pytest", "tkinter"], noarchive=False)
    pyz = PYZ(analysis.pure)
    exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name=name,
              console=console, debug=False, strip=False, upx=False)
    executables.append(exe)
    binaries.extend(analysis.binaries)
    datas.extend(analysis.datas)
collect = COLLECT(*executables, binaries, datas, strip=False, upx=False, name="Tocktail")
