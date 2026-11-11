import os
from pathlib import Path


def default_database() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return root / "Tocktail" / "tocktail.sqlite3"
