from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_NATURAL_PARTS = re.compile(r"(\d+)")


def _natural_text_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in _NATURAL_PARTS.split(value)
        if part
    )


def _natural_nocase(left: str, right: str) -> int:
    left_key = _natural_text_key(left)
    right_key = _natural_text_key(right)
    if left_key != right_key:
        return (left_key > right_key) - (left_key < right_key)
    left_folded = left.casefold()
    right_folded = right.casefold()
    return (left_folded > right_folded) - (left_folded < right_folded)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=15.0,
            factory=ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.create_collation("NATURAL_NOCASE", _natural_nocase)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
