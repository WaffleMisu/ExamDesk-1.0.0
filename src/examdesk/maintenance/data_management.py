from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from examdesk.db import AdminRepository
from examdesk.domain.enums import QuestionStatus, UsageScope
from examdesk.version import __version__

from .backup import BackupService


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned:
        raise ValueError("操作原因不能为空")
    return cleaned


def _ids(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _placeholders(values: tuple[str, ...]) -> str:
    if not values:
        raise ValueError("没有选择任何记录")
    return ",".join("?" for _ in values)


def _audit(connection, actor_id: str, action: str, entity_type: str, details: dict) -> None:
    connection.execute(
        """
        INSERT INTO audit_events(
            id, actor_id, action, entity_type, entity_id, details_json, created_at
        ) VALUES (?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            str(uuid4()),
            actor_id,
            action,
            entity_type,
            json.dumps(details, ensure_ascii=False, sort_keys=True),
            _now(),
        ),
    )


@dataclass(frozen=True, slots=True)
class OrphanAttempt:
    id: str
    session_id: str
    candidate_name: str
    started_at: str
    state_filename: str
    issue: str


@dataclass(frozen=True, slots=True)
class QuestionDeleteResult:
    deleted_ids: tuple[str, ...]
    disabled_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionDeleteResult:
    deleted_ids: tuple[str, ...]
    archived_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttemptDeleteImpact:
    attempts: int
    answers: int
    reviews: int
    foreground_events: int
    package_imports: int = 0


class SafetyBackupService:
    def __init__(self, database, asset_root: Path, key_store, directory: Path | None = None) -> None:
        self.database = database
        self.asset_root = asset_root.resolve()
        self.key_store = key_store
        self.directory = (directory or self.asset_root.parent / "safety_backups").resolve()

    def create(self, *, password: str, operation: str) -> Path:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", operation).strip("_") or "operation"
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / "{}_{}.exambackup".format(
            datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            cleaned,
        )
        BackupService(self.database, self.asset_root).create(
            destination,
            password=password,
            key_store=self.key_store,
            software_version=__version__,
            automatic=True,
        )
        self._prune()
        return destination

    def _prune(self, keep: int = 5) -> None:
        backups = sorted(
            self.directory.glob("*.exambackup"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in backups[keep:]:
            path.unlink(missing_ok=True)


class OrphanAttemptService:
    def __init__(self, database, state_directories: list[Path]) -> None:
        self.database = database
        self.state_directories = [path.resolve() for path in state_directories]
        self.admins = AdminRepository(database)

    def list_unrecoverable(self) -> list[OrphanAttempt]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.id, a.session_id, a.candidate_name, a.started_at,
                       a.state_filename, a.state_error, s.status AS session_status
                FROM attempts a
                LEFT JOIN sessions s ON s.id = a.session_id
                WHERE a.status = 'active'
                ORDER BY a.started_at
                """
            ).fetchall()
        result = []
        for row in rows:
            filename = row["state_filename"] or ""
            if row["session_status"] is None:
                result.append(
                    OrphanAttempt(
                        row["id"],
                        row["session_id"],
                        row["candidate_name"],
                        row["started_at"],
                        filename,
                        "所属场次不存在",
                    )
                )
                continue
            if row["session_status"] == "archived":
                result.append(
                    OrphanAttempt(
                        row["id"],
                        row["session_id"],
                        row["candidate_name"],
                        row["started_at"],
                        filename,
                        "所属场次已归档",
                    )
                )
                continue
            paths = self._candidate_paths(filename)
            existing = [path for path in paths if path.is_file()]
            if row["state_error"]:
                result.append(
                    OrphanAttempt(
                        row["id"],
                        row["session_id"],
                        row["candidate_name"],
                        row["started_at"],
                        filename,
                        "状态文件无法解密或内容损坏",
                    )
                )
            elif not existing:
                result.append(
                    OrphanAttempt(
                        row["id"],
                        row["session_id"],
                        row["candidate_name"],
                        row["started_at"],
                        filename,
                        "状态文件缺失",
                    )
                )
            elif filename and all(not _looks_like_exam_state(path) for path in existing):
                result.append(
                    OrphanAttempt(
                        row["id"],
                        row["session_id"],
                        row["candidate_name"],
                        row["started_at"],
                        filename,
                        "状态文件损坏",
                    )
                )
        return result

    def void(
        self,
        attempt_id: str,
        *,
        actor_id: str,
        password: str,
        reason: str,
    ) -> None:
        self.admins.verify_password(actor_id, password)
        cleaned_reason = _clean_reason(reason)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT state_filename FROM attempts WHERE id = ? AND status = 'active'",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("考试不存在或已不在进行中")
            filename = row["state_filename"] or ""
            connection.execute(
                """
                UPDATE attempts SET status = 'void', is_void = 1, void_reason = ?
                WHERE id = ? AND status = 'active'
                """,
                (cleaned_reason, attempt_id),
            )
            _audit(
                connection,
                actor_id,
                "void_unrecoverable_attempt",
                "attempt",
                {"attempt_id": attempt_id, "reason": cleaned_reason},
            )
        for path in self._candidate_paths(filename):
            path.unlink(missing_ok=True)

    def _candidate_paths(self, filename: str) -> list[Path]:
        if filename:
            return [directory / filename for directory in self.state_directories]
        result = []
        for directory in self.state_directories:
            result.extend(directory.glob("active_exam*.state"))
        return result


class DataManagementService:
    def __init__(self, database) -> None:
        self.database = database
        self.admins = AdminRepository(database)

    def delete_questions(
        self,
        question_ids: Iterable[str],
        *,
        actor_id: str,
        password: str,
        reason: str,
        backup: Callable[[], object],
        asset_root: Path | None = None,
    ) -> QuestionDeleteResult:
        selected = _ids(question_ids)
        placeholders = _placeholders(selected)
        self.admins.verify_password(actor_id, password, supervisor_only=True)
        cleaned_reason = _clean_reason(reason)
        backup()
        orphan_files: list[str] = []
        with self.database.transaction(immediate=True) as connection:
            existing = {
                row["id"]
                for row in connection.execute(
                    f"SELECT id FROM questions WHERE id IN ({placeholders})", selected
                ).fetchall()
            }
            referenced = {
                row["question_id"]
                for row in connection.execute(
                    f"SELECT DISTINCT question_id FROM session_questions WHERE question_id IN ({placeholders})",
                    selected,
                ).fetchall()
            }
            disabled = tuple(value for value in selected if value in existing and value in referenced)
            deleted = tuple(value for value in selected if value in existing and value not in referenced)
            candidate_assets = []
            if deleted:
                candidate_assets = connection.execute(
                    f"""
                    SELECT DISTINCT a.id, a.relative_path
                    FROM assets a
                    JOIN question_asset_links qal ON qal.asset_id = a.id
                    WHERE qal.question_id IN ({_placeholders(deleted)})
                    """,
                    deleted,
                ).fetchall()
            if disabled:
                disabled_marks = _placeholders(disabled)
                connection.execute(
                    f"UPDATE questions SET status = ?, updated_at = ? WHERE id IN ({disabled_marks})",
                    (QuestionStatus.DISABLED.value, _now(), *disabled),
                )
            if deleted:
                connection.execute(f"DELETE FROM questions WHERE id IN ({_placeholders(deleted)})", deleted)
                for asset in candidate_assets:
                    in_use = connection.execute(
                        "SELECT 1 FROM question_asset_links WHERE asset_id = ? LIMIT 1",
                        (asset["id"],),
                    ).fetchone()
                    if in_use is None:
                        connection.execute("DELETE FROM assets WHERE id = ?", (asset["id"],))
                        orphan_files.append(asset["relative_path"])
            _audit(
                connection,
                actor_id,
                "delete_questions",
                "question",
                {
                    "reason": cleaned_reason,
                    "requested_ids": list(selected),
                    "deleted_ids": list(deleted),
                    "disabled_ids": list(disabled),
                },
            )
            _bump_bank_revision(connection)
        if asset_root is not None:
            root = asset_root.resolve()
            for relative in orphan_files:
                path = (root / relative).resolve()
                if root == path or root in path.parents:
                    path.unlink(missing_ok=True)
        return QuestionDeleteResult(deleted, disabled)

    def batch_update_questions(
        self,
        question_ids: Iterable[str],
        *,
        actor_id: str,
        changes: dict,
        tags_mode: str = "replace",
    ) -> int:
        selected = _ids(question_ids)
        placeholders = _placeholders(selected)
        self.admins.require_active(actor_id)
        allowed = {
            "status",
            "usage_scope",
            "applicable_year",
            "source",
            "chapter",
            "clause",
            "difficulty",
            "tags",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError("不支持批量修改：{}".format("、".join(sorted(unknown))))
        if not changes:
            raise ValueError("没有选择要修改的字段")
        normalized = dict(changes)
        if "status" in normalized:
            normalized["status"] = QuestionStatus(normalized["status"]).value
        if "usage_scope" in normalized:
            normalized["usage_scope"] = UsageScope(normalized["usage_scope"]).value
        if "applicable_year" in normalized:
            year = normalized["applicable_year"]
            if year is not None and not 1 <= int(year) <= 9999:
                raise ValueError("适用年份范围无效")
            normalized["applicable_year"] = int(year) if year is not None else None
        tags = tuple(dict.fromkeys(str(tag).strip() for tag in normalized.pop("tags", ()) if str(tag).strip()))
        if tags_mode not in {"replace", "append", "remove"}:
            raise ValueError("标签修改方式无效")
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                f"SELECT id, tags_json FROM questions WHERE id IN ({placeholders})", selected
            ).fetchall()
            set_parts = [f"{field} = ?" for field in normalized]
            if set_parts:
                connection.execute(
                    f"UPDATE questions SET {', '.join(set_parts)}, updated_at = ? WHERE id IN ({placeholders})",
                    (*normalized.values(), now, *selected),
                )
            if "tags" in changes:
                for row in existing:
                    current = list(json.loads(row["tags_json"]))
                    if tags_mode == "replace":
                        updated = list(tags)
                    elif tags_mode == "append":
                        updated = list(dict.fromkeys([*current, *tags]))
                    else:
                        remove = set(tags)
                        updated = [tag for tag in current if tag not in remove]
                    connection.execute(
                        "UPDATE questions SET tags_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(updated, ensure_ascii=False), now, row["id"]),
                    )
            _audit(
                connection,
                actor_id,
                "batch_update_questions",
                "question",
                {
                    "question_ids": list(selected),
                    "changes": _json_safe_changes(changes),
                    "tags_mode": tags_mode,
                },
            )
            _bump_bank_revision(connection)
        return len(existing)

    def delete_sessions(
        self,
        session_ids: Iterable[str],
        *,
        actor_id: str,
        password: str,
        reason: str,
        backup: Callable[[], object],
    ) -> SessionDeleteResult:
        selected = _ids(session_ids)
        placeholders = _placeholders(selected)
        self.admins.verify_password(actor_id, password, supervisor_only=True)
        cleaned_reason = _clean_reason(reason)
        backup()
        with self.database.transaction(immediate=True) as connection:
            existing = {
                row["id"]
                for row in connection.execute(
                    f"SELECT id FROM sessions WHERE id IN ({placeholders})", selected
                ).fetchall()
            }
            referenced = {
                row["session_id"]
                for row in connection.execute(
                    f"SELECT DISTINCT session_id FROM attempts WHERE session_id IN ({placeholders})",
                    selected,
                ).fetchall()
            }
            archived = tuple(value for value in selected if value in existing and value in referenced)
            deleted = tuple(value for value in selected if value in existing and value not in referenced)
            if archived:
                connection.execute(
                    f"UPDATE sessions SET status = 'archived' WHERE id IN ({_placeholders(archived)})",
                    archived,
                )
            if deleted:
                connection.execute(f"DELETE FROM sessions WHERE id IN ({_placeholders(deleted)})", deleted)
            _audit(
                connection,
                actor_id,
                "delete_sessions",
                "session",
                {
                    "reason": cleaned_reason,
                    "requested_ids": list(selected),
                    "deleted_ids": list(deleted),
                    "archived_ids": list(archived),
                },
            )
        return SessionDeleteResult(deleted, archived)

    def attempt_delete_impact(self, attempt_ids: Iterable[str]) -> AttemptDeleteImpact:
        selected = _ids(attempt_ids)
        placeholders = _placeholders(selected)
        with self.database.connect() as connection:
            attempts = connection.execute(
                f"SELECT COUNT(*) FROM attempts WHERE id IN ({placeholders})", selected
            ).fetchone()[0]
            answers = connection.execute(
                f"SELECT COUNT(*) FROM attempt_answers WHERE attempt_id IN ({placeholders})", selected
            ).fetchone()[0]
            reviews = connection.execute(
                f"SELECT COUNT(*) FROM score_reviews WHERE attempt_id IN ({placeholders})", selected
            ).fetchone()[0]
            events = connection.execute(
                f"SELECT COUNT(*) FROM foreground_events WHERE attempt_id IN ({placeholders})", selected
            ).fetchone()[0]
            imports = connection.execute(
                f"""
                SELECT COUNT(*) FROM package_imports
                WHERE package_kind = 'result' AND package_id IN ({placeholders})
                """,
                selected,
            ).fetchone()[0]
        return AttemptDeleteImpact(attempts, answers, reviews, events, imports)

    def delete_attempts(
        self,
        attempt_ids: Iterable[str],
        *,
        actor_id: str,
        password: str,
        reason: str,
        backup: Callable[[], object],
    ) -> AttemptDeleteImpact:
        selected = _ids(attempt_ids)
        placeholders = _placeholders(selected)
        self.admins.verify_password(actor_id, password, supervisor_only=True)
        cleaned_reason = _clean_reason(reason)
        impact = self.attempt_delete_impact(selected)
        backup()
        with self.database.transaction(immediate=True) as connection:
            summaries = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT id, session_id, candidate_name, status, started_at, submitted_at
                    FROM attempts WHERE id IN ({placeholders})
                    """,
                    selected,
                ).fetchall()
            ]
            connection.execute(f"DELETE FROM score_reviews WHERE attempt_id IN ({placeholders})", selected)
            connection.execute(
                f"""
                DELETE FROM package_imports
                WHERE package_kind = 'result' AND package_id IN ({placeholders})
                """,
                selected,
            )
            connection.execute(f"DELETE FROM attempts WHERE id IN ({placeholders})", selected)
            _audit(
                connection,
                actor_id,
                "delete_attempts",
                "attempt",
                {
                    "reason": cleaned_reason,
                    "records": summaries,
                    "impact": {
                        "attempts": impact.attempts,
                        "answers": impact.answers,
                        "reviews": impact.reviews,
                        "foreground_events": impact.foreground_events,
                        "package_imports": impact.package_imports,
                    },
                },
            )
        return impact


def _looks_like_exam_state(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return len(data) >= 36 and data.startswith(b"EXDKST10")


def _bump_bank_revision(connection) -> None:
    now = _now()
    row = connection.execute(
        "SELECT value_json FROM app_settings WHERE key = 'bank_revision'"
    ).fetchone()
    revision = (int(json.loads(row["value_json"])) if row else 0) + 1
    connection.execute(
        """
        INSERT INTO app_settings(key, value_json, updated_at) VALUES ('bank_revision', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
            updated_at = excluded.updated_at
        """,
        (json.dumps(revision), now),
    )


def _json_safe_changes(changes: dict) -> dict:
    result = {}
    for key, value in changes.items():
        if hasattr(value, "value"):
            value = value.value
        if isinstance(value, (tuple, set)):
            value = list(value)
        result[key] = value
    return result
