from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from examdesk.packages import PackageError, RecipientPackageCodec, X25519KeyPair

from .models import BatchImportResult, ImportedResult


class ResultImportService:
    def __init__(self, database, master_recipient: X25519KeyPair) -> None:
        self.database = database
        self.master_recipient = master_recipient

    def import_folder(self, folder: Path, *, imported_by: str) -> BatchImportResult:
        result = BatchImportResult()
        for path in sorted(folder.glob("*.examresult")):
            result.items.append(self.import_file(path, imported_by=imported_by))
        return result

    def import_file(self, path: Path, *, imported_by: str) -> ImportedResult:
        try:
            package = path.read_bytes()
        except OSError as exc:
            return ImportedResult(path, None, None, False, error=str(exc))
        file_hash = hashlib.sha256(package).hexdigest()
        with self.database.connect() as connection:
            duplicate = connection.execute(
                "SELECT package_id FROM package_imports WHERE file_hash = ?",
                (file_hash,),
            ).fetchone()
            sessions = connection.execute(
                """
                SELECT id, session_auth_key, max_attempts FROM sessions
                WHERE session_auth_key IS NOT NULL
                """
            ).fetchall()
        if duplicate is not None:
            return ImportedResult(path, duplicate["package_id"], None, False, duplicate_file=True)

        decoded = None
        matched_session = None
        for session in sessions:
            try:
                candidate = RecipientPackageCodec.decode(
                    package,
                    recipient=self.master_recipient,
                    session_auth_key=session["session_auth_key"],
                    expected_kind="result",
                )
            except PackageError:
                continue
            decoded = candidate
            matched_session = session
            break
        if decoded is None or matched_session is None:
            return ImportedResult(path, None, None, False, error="结果文件不属于当前题库中的任何场次")

        try:
            payload = decoded.json()
            _validate_result_payload(payload, matched_session["id"])
            duplicate_candidate = self._save_result(
                payload,
                file_hash=file_hash,
                source_path=path,
                imported_by=imported_by,
                max_attempts=matched_session["max_attempts"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            return ImportedResult(path, None, None, False, error=f"结果内容无效：{exc}")
        return ImportedResult(
            source_path=path,
            attempt_id=str(payload["attempt_id"]),
            candidate_name=str(payload["candidate_name"]),
            imported=True,
            duplicate_candidate=duplicate_candidate,
        )

    def _save_result(
        self,
        payload: dict,
        *,
        file_hash: str,
        source_path: Path,
        imported_by: str,
        max_attempts: int,
    ) -> bool:
        attempt_id = str(payload["attempt_id"])
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT id FROM attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError("作答UUID已经存在")
            connection.execute(
                """
                INSERT INTO attempts(
                    id, session_id, candidate_name, machine_name, windows_user,
                    software_version, status, started_at, deadline_at, submitted_at,
                    submit_reason, strict_score, estimated_score, max_score,
                    question_order_json, monitor_status, source_file_hash,
                    imported_at, created_at, time_anomaly
                ) VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    payload["session_id"],
                    payload["candidate_name"],
                    payload["machine_name"],
                    payload["windows_user"],
                    payload["software_version"],
                    payload["started_at"],
                    payload.get("deadline_at"),
                    payload["submitted_at"],
                    payload["submit_reason"],
                    payload["strict_score"],
                    payload["estimated_score"],
                    payload["max_score"],
                    _json([item["question_id"] for item in payload["questions"]]),
                    payload["monitor_status"],
                    file_hash,
                    now,
                    now,
                    int(bool(payload.get("time_anomaly"))),
                ),
            )
            for item in payload["questions"]:
                connection.execute(
                    """
                    INSERT INTO attempt_answers(
                        attempt_id, question_id, display_order, option_order_json,
                        response_json, strict_score, estimated_score, similar_flags_json,
                        answered_at, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        item["question_id"],
                        item["display_order"],
                        _json(item.get("option_order", [])),
                        _json(item.get("response")),
                        item["strict_score"],
                        item["estimated_score"],
                        _json(item.get("similar_flags", [])),
                        payload["submitted_at"] if item.get("response") not in (None, [], "") else None,
                        _json(item.get("snapshot", {})),
                    ),
                )
            connection.executemany(
                """
                INSERT INTO foreground_events(
                    id, attempt_id, started_at, ended_at, duration_seconds,
                    application_name, process_name, window_title, event_kind, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid4()),
                        attempt_id,
                        item["started_at"],
                        item["ended_at"],
                        item["duration_seconds"],
                        item.get("application_name", ""),
                        item.get("process_name", ""),
                        item.get("window_title", ""),
                        item.get("event_kind", "window"),
                        now,
                    )
                    for item in payload.get("foreground_events", [])
                ],
            )
            connection.execute(
                """
                INSERT INTO package_imports(
                    id, package_kind, package_id, file_hash, source_path,
                    imported_by, imported_at, result_json
                ) VALUES (?, 'result', ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    attempt_id,
                    file_hash,
                    str(source_path),
                    imported_by,
                    now,
                    _json({"candidate_name": payload["candidate_name"]}),
                ),
            )
            attempt_count = connection.execute(
                """
                SELECT COUNT(*) FROM attempts
                WHERE session_id = ? AND candidate_name = ? COLLATE NOCASE
                  AND status IN ('submitted', 'incomplete') AND is_void = 0
                """,
                (payload["session_id"], payload["candidate_name"]),
            ).fetchone()[0]
            duplicate_candidate = attempt_count > max_attempts
            if duplicate_candidate:
                connection.execute(
                    """
                    INSERT INTO audit_events(
                        id, actor_id, action, entity_type, entity_id, details_json, created_at
                    ) VALUES (?, ?, 'duplicate_attempt_detected', 'attempt', ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        imported_by,
                        attempt_id,
                        _json({"attempt_count": attempt_count, "max_attempts": max_attempts}),
                        now,
                    ),
                )
        return duplicate_candidate


def _validate_result_payload(payload: dict, expected_session_id: str) -> None:
    required = {
        "attempt_id",
        "session_id",
        "candidate_name",
        "machine_name",
        "windows_user",
        "software_version",
        "started_at",
        "submitted_at",
        "submit_reason",
        "strict_score",
        "estimated_score",
        "max_score",
        "monitor_status",
        "questions",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"缺少字段：{'、'.join(sorted(missing))}")
    if payload.get("schema_version") != 1:
        raise ValueError("结果格式版本不受支持")
    if payload["session_id"] != expected_session_id:
        raise ValueError("场次编号与加密证明不一致")
    if not isinstance(payload["questions"], list) or not payload["questions"]:
        raise ValueError("结果没有题目明细")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
