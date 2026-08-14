from __future__ import annotations

import contextlib
import json
import os
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

from examdesk.domain.enums import AttemptStatus, SubmitReason
from examdesk.exam import ExamGrader, ExamState, ExamStateStore
from examdesk.monitoring import FocusEvent
from examdesk.packages import RecipientPackageCodec
from examdesk.paths import safe_file_part
from examdesk.questions import question_to_payload
from examdesk.sessions import ExamDefinition

from .local_review import SubmittedReviewStore
from .models import ResultArtifact


class AttemptError(ValueError):
    pass


class AttemptService:
    def __init__(
        self,
        database,
        state_store: ExamStateStore,
        review_store: SubmittedReviewStore | None = None,
    ) -> None:
        self.database = database
        self.state_store = state_store
        self.review_store = review_store or SubmittedReviewStore(
            state_store.directories[0] / "submitted_reviews"
        )

    def start(
        self,
        definition: ExamDefinition,
        *,
        candidate_name: str,
        software_version: str,
        machine_name: str | None = None,
        windows_user: str | None = None,
        now: datetime | None = None,
    ) -> ExamState:
        cleaned_name = definition.validate_candidate(candidate_name)
        with self.database.connect() as connection:
            active = connection.execute(
                "SELECT id FROM attempts WHERE status = 'active' LIMIT 1"
            ).fetchone()
            count = connection.execute(
                """
                SELECT COUNT(*) FROM attempts
                WHERE session_id = ? AND candidate_name = ? COLLATE NOCASE
                  AND status IN ('submitted', 'incomplete')
                """,
                (definition.session_id, cleaned_name),
            ).fetchone()[0]
        if active is not None:
            raise AttemptError("本机存在未完成考试，必须先恢复")
        if count >= definition.max_attempts:
            raise AttemptError("已达到本场考试最大答题次数")
        state = ExamState.create(
            definition,
            candidate_name=cleaned_name,
            machine_name=machine_name or socket.gethostname(),
            windows_user=windows_user or os.environ.get("USERNAME", ""),
            software_version=software_version,
            now=now,
        )
        self.state_store.save(state)
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO attempts(
                        id, session_id, candidate_name, machine_name, windows_user,
                        software_version, status, started_at, deadline_at, max_score,
                        question_order_json, created_at, package_id, state_filename
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.attempt_id,
                        state.session_id,
                        state.candidate_name,
                        state.machine_name,
                        state.windows_user,
                        state.software_version,
                        state.started_at.isoformat(),
                        state.deadline_at.isoformat() if state.deadline_at else None,
                        str(definition.max_score),
                        _json([item.question_id for item in state.displayed_questions]),
                        state.started_at.isoformat(),
                        definition.package_id,
                        self.state_store.filename,
                    ),
                )
        except Exception:
            self.state_store.clear()
            raise
        return state

    def mark_state_error(self, package_id: str, message: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE attempts SET state_error = ?
                WHERE status = 'active' AND package_id = ?
                """,
                (message.strip()[:500], package_id),
            )

    def clear_state_error(self, package_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE attempts SET state_error = ''
                WHERE status = 'active' AND package_id = ? AND state_error != ''
                """,
                (package_id,),
            )

    def checkpoint(self, state: ExamState) -> None:
        if state.status is not AttemptStatus.ACTIVE:
            raise AttemptError("只有进行中的考试可以保存")
        self.state_store.save(state)

    def finalize(
        self,
        definition: ExamDefinition,
        state: ExamState,
        *,
        reason: SubmitReason,
        foreground_events: list[FocusEvent],
        monitor_status: str,
        local_result_directory: Path,
        submission_directory: Path,
        submitted_at: datetime | None = None,
    ) -> ResultArtifact:
        grade = ExamGrader.grade(definition, state)
        actual_submitted_at = (submitted_at or datetime.now(UTC)).astimezone(UTC)
        state.submit(reason, actual_submitted_at)
        try:
            payload = _result_payload(
                definition,
                state,
                grade,
                foreground_events,
                monitor_status,
            )
            package = RecipientPackageCodec.encode_json(
                payload,
                package_kind="result",
                recipient_public_key=X25519PublicKey.from_public_bytes(
                    definition.result_recipient_public_key
                ),
                session_auth_key=definition.session_auth_key,
                package_id=state.attempt_id,
            )
            file_name = _result_file_name(definition.name, state)
            local_path = local_result_directory / file_name
            local_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(local_path, package)

            submission_path = submission_directory / file_name
            submission_error = None
            try:
                submission_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(submission_path, package)
            except OSError as exc:
                submission_error = str(exc)
                submission_path = None

            local_review_path = self.review_store.save(definition, payload)
            self._save_submitted_attempt(definition, state, grade, foreground_events, monitor_status)
            self.state_store.clear()
            return ResultArtifact(
                attempt_id=state.attempt_id,
                local_backup=local_path,
                local_review=local_review_path,
                submission_file=submission_path,
                grade=grade,
                submission_error=submission_error,
            )
        except Exception:
            state.status = AttemptStatus.ACTIVE
            state.submitted_at = None
            state.submit_reason = None
            self.state_store.save(state)
            raise

    def _save_submitted_attempt(self, definition, state, grade, foreground_events, monitor_status) -> None:
        grade_by_id = {item.question_id: item for item in grade.questions}
        displayed_by_id = {item.question_id: item for item in state.displayed_questions}
        questions_by_id = {item.question_id: item.question for item in definition.questions}
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE attempts SET
                    status = 'submitted', submitted_at = ?, submit_reason = ?,
                    strict_score = ?, estimated_score = ?, monitor_status = ?, time_anomaly = ?
                WHERE id = ?
                """,
                (
                    state.submitted_at.isoformat(),
                    state.submit_reason.value,
                    str(grade.strict_score),
                    str(grade.estimated_score),
                    monitor_status,
                    int(state.time_anomaly),
                    state.attempt_id,
                ),
            )
            for question_id, question_grade in grade_by_id.items():
                displayed = displayed_by_id[question_id]
                connection.execute(
                    """
                    INSERT INTO attempt_answers(
                        attempt_id, question_id, display_order, option_order_json,
                        response_json, strict_score, estimated_score, similar_flags_json,
                        answered_at, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.attempt_id,
                        question_id,
                        displayed.display_order,
                        _json(list(displayed.option_order)),
                        _json(state.responses.get(question_id)),
                        str(question_grade.strict_score),
                        str(question_grade.estimated_score),
                        _json(_similar_flags(question_grade)),
                        state.submitted_at.isoformat() if question_id in state.responses else None,
                        _json(question_to_payload(questions_by_id[question_id])),
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
                        state.attempt_id,
                        event.started_at.isoformat(),
                        event.ended_at.isoformat(),
                        event.duration_seconds,
                        event.application_name,
                        event.process_name,
                        event.window_title,
                        event.event_kind,
                        state.submitted_at.isoformat(),
                    )
                    for event in foreground_events
                ],
            )


def _result_payload(definition, state, grade, foreground_events, monitor_status) -> dict:
    grade_by_id = {item.question_id: item for item in grade.questions}
    return {
        "schema_version": 1,
        "attempt_id": state.attempt_id,
        "session_id": state.session_id,
        "package_id": state.package_id,
        "session_name": definition.name,
        "candidate_name": state.candidate_name,
        "machine_name": state.machine_name,
        "windows_user": state.windows_user,
        "software_version": state.software_version,
        "started_at": state.started_at.isoformat(),
        "deadline_at": state.deadline_at.isoformat() if state.deadline_at else None,
        "submitted_at": state.submitted_at.isoformat(),
        "submit_reason": state.submit_reason.value,
        "time_anomaly": state.time_anomaly,
        "monitor_status": monitor_status,
        "strict_score": str(grade.strict_score),
        "estimated_score": str(grade.estimated_score),
        "max_score": str(grade.max_score),
        "max_attempts": definition.max_attempts,
        "questions": [
            {
                "question_id": displayed.question_id,
                "question_version": displayed.question_version,
                "display_order": displayed.display_order,
                "option_order": list(displayed.option_order),
                "response": state.responses.get(displayed.question_id),
                "strict_score": str(grade_by_id[displayed.question_id].strict_score),
                "estimated_score": str(grade_by_id[displayed.question_id].estimated_score),
                "similar_flags": _similar_flags(grade_by_id[displayed.question_id]),
                "snapshot": question_to_payload(state.question_at(definition, displayed.display_order - 1).question),
            }
            for displayed in state.displayed_questions
        ],
        "foreground_events": [
            {
                "started_at": event.started_at.isoformat(),
                "ended_at": event.ended_at.isoformat(),
                "duration_seconds": event.duration_seconds,
                "application_name": event.application_name,
                "process_name": event.process_name,
                "window_title": event.window_title,
                "event_kind": event.event_kind,
            }
            for event in foreground_events
        ],
    }


def _similar_flags(question_grade) -> list[dict]:
    return [
        {
            "blank_index": item.response_index,
            "expected_index": item.expected_index,
            "response": item.response,
            "accepted_answer": item.similar_match.accepted_answer,
            "similarity": round(item.similar_match.similarity, 2),
            "strict_score": str(item.strict_score),
            "estimated_score": str(item.estimated_score),
        }
        for item in question_grade.blank_grades
        if item.similar_match is not None
    ]


def _result_file_name(session_name: str, state: ExamState) -> str:
    timestamp = state.submitted_at.astimezone().strftime("%Y%m%d_%H%M%S")
    prefix = f"{safe_file_part(session_name)}_{safe_file_part(state.candidate_name)}"
    return f"{prefix}_{timestamp}_{state.attempt_id[:8]}.examresult"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="result-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise
