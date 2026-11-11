from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from tocktail.browser.protocol import EXTENSION_ID
from tocktail.config.paths import default_database


def config_path() -> Path:
    return default_database().parent / "browser-host.json"


@dataclass(frozen=True)
class BridgeConfig:
    database: Path
    chrome_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.chrome_ids and not self.edge_ids:
            raise ValueError("At least one extension ID is required")
        if any(not EXTENSION_ID.fullmatch(value) for value in self.chrome_ids + self.edge_ids):
            raise ValueError("Invalid extension ID")

    def allows(self, family: str, origin: str) -> bool:
        ids = self.chrome_ids if family == "chrome" else self.edge_ids if family == "edge" else ()
        return origin in {f"chrome-extension://{value}/" for value in ids}

    def pipe_name(self, sid: str) -> str:
        key = sid + "\n" + os.path.normcase(str(self.database.resolve()))
        return "\\\\.\\pipe\\Tocktail-" + hashlib.sha256(key.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, object]:
        return {"version": 1, "database": str(self.database.resolve()),
                "chrome_ids": list(self.chrome_ids), "edge_ids": list(self.edge_ids)}

    @classmethod
    def load(cls, path: Path | None = None) -> "BridgeConfig":
        value = json.loads((path or config_path()).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("Invalid browser host configuration")
        for key in ("chrome_ids", "edge_ids"):
            if not isinstance(value.get(key), list) or not all(isinstance(item, str) for item in value[key]):
                raise ValueError("Invalid configured extension IDs")
        if not isinstance(value.get("database"), str) or not Path(value["database"]).is_absolute():
            raise ValueError("Invalid configured database path")
        return cls(Path(value["database"]), tuple(value["chrome_ids"]), tuple(value["edge_ids"]))
