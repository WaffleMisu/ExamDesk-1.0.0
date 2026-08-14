from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from examdesk.db import AdminRepository


@dataclass(frozen=True, slots=True)
class ResetPreview:
    administrators: int
    questions: int
    sessions: int
    attempts: int
    assets: int
    root: Path


class FactoryResetService:
    def __init__(self, database, root: Path) -> None:
        self.database = database
        self.root = root.expanduser().resolve()
        self.admins = AdminRepository(database)
        if self.database.path.expanduser().resolve().parent != self.root:
            raise ValueError("数据库不在软件数据目录中，禁止重置")
        if self.root.parent == self.root or not self.root.name:
            raise ValueError("软件数据目录无效")

    def preview(self) -> ResetPreview:
        with self.database.connect() as connection:
            values = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("administrators", "questions", "sessions", "attempts", "assets")
            }
        return ResetPreview(
            values["administrators"],
            values["questions"],
            values["sessions"],
            values["attempts"],
            values["assets"],
            self.root,
        )

    def authenticate_and_record(self, actor_id: str, password: str, *, skipped_backup: bool) -> None:
        self.admins.verify_password(actor_id, password, supervisor_only=True)
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, actor_id, action, entity_type, entity_id, details_json, created_at
                ) VALUES (?, ?, 'factory_reset_requested', 'application', NULL, ?, ?)
                """,
                (
                    str(uuid4()),
                    actor_id,
                    json.dumps({"skipped_backup": skipped_backup}, ensure_ascii=False),
                    now,
                ),
            )

    def stage_reset(self) -> Path:
        if not self.root.is_dir():
            raise ValueError("软件数据目录不存在")
        staged = self.root.with_name(f".{self.root.name}-reset-{uuid4().hex}")
        try:
            os.replace(self.root, staged)
            self.root.mkdir(parents=False, exist_ok=False)
        except Exception:
            if staged.exists() and not self.root.exists():
                os.replace(staged, self.root)
            raise
        return staged
