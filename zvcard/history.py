"""Remember which files were already imported (so you can 'select only new')."""
from __future__ import annotations

import json
import os
from pathlib import Path


def default_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "ZVCardTool" / "history.json"


def _key(name: str, size: int, mtime: float) -> str:
    return f"{name.upper()}|{size}|{int(mtime)}"


class History:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def is_imported(self, item) -> bool:
        m = item.main
        return _key(m.path.name, m.size, m.mtime) in self.data

    def mark(self, item, dest: str = "") -> None:
        m = item.main
        self.data[_key(m.path.name, m.size, m.mtime)] = dest

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
