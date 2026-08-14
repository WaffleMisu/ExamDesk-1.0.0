from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PRODUCT_DIR_NAME = "ExamDesk"
INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_file_part(value: str, fallback: str = "未命名") -> str:
    cleaned = INVALID_FILE_CHARS.sub("_", value.strip()).rstrip(". ")
    return cleaned[:80] or fallback


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    app: Path
    state: Path
    results: Path
    practice: Path
    assets: Path
    logs: Path
    updates: Path
    database: Path

    @classmethod
    def from_root(cls, root: Path) -> AppPaths:
        resolved = root.expanduser().resolve()
        return cls(
            root=resolved,
            app=resolved / "app",
            state=resolved / "state",
            results=resolved / "results",
            practice=resolved / "practice",
            assets=resolved / "assets",
            logs=resolved / "logs",
            updates=resolved / "updates",
            database=resolved / "data.sqlite3",
        )

    @classmethod
    def for_current_user(cls) -> AppPaths:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return cls.from_root(base / PRODUCT_DIR_NAME)

    def ensure(self) -> None:
        for directory in (
            self.root,
            self.app,
            self.state,
            self.results,
            self.practice,
            self.assets,
            self.logs,
            self.updates,
        ):
            directory.mkdir(parents=True, exist_ok=True)
