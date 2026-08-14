from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from examdesk.domain.enums import AttemptStatus, QuestionType, SubmitReason
from examdesk.sessions import ExamDefinition, SessionQuestion

STATE_MAGIC = b"EXDKST10"


class ExamStateStoreError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DisplayedQuestion:
    question_id: str
    question_version: int
    display_order: int
    option_order: tuple[str, ...]


@dataclass(slots=True)
class ExamState:
    attempt_id: str
    session_id: str
    package_id: str
    candidate_name: str
    machine_name: str
    windows_user: str
    software_version: str
    status: AttemptStatus
    started_at: datetime
    deadline_at: datetime | None
    last_effective_at: datetime
    displayed_questions: list[DisplayedQuestion]
    responses: dict[str, object] = field(default_factory=dict)
    marked_question_ids: set[str] = field(default_factory=set)
    warnings_shown: set[int] = field(default_factory=set)
    current_index: int = 0
    time_anomaly: bool = False
    submitted_at: datetime | None = None
    submit_reason: SubmitReason | None = None

    @classmethod
    def create(
        cls,
        definition: ExamDefinition,
        *,
        candidate_name: str,
        machine_name: str,
        windows_user: str,
        software_version: str,
        now: datetime | None = None,
        attempt_id: str | None = None,
    ) -> ExamState:
        started_at = (now or datetime.now(UTC)).astimezone(UTC)
        actual_attempt_id = attempt_id or str(uuid4())
        seed = hashlib.sha256(
            f"{definition.random_seed}:{actual_attempt_id}".encode()
        ).hexdigest()
        rng = random.Random(seed)
        session_questions = list(definition.questions)
        rng.shuffle(session_questions)
        displayed = []
        for display_order, item in enumerate(session_questions, start=1):
            keys = [option.key for option in item.question.options]
            if item.question.question_type in (QuestionType.SINGLE, QuestionType.MULTIPLE):
                rng.shuffle(keys)
            displayed.append(
                DisplayedQuestion(
                    question_id=item.question_id,
                    question_version=item.question_version,
                    display_order=display_order,
                    option_order=tuple(keys),
                )
            )
        deadline = (
            started_at + timedelta(minutes=definition.duration_minutes)
            if definition.duration_minutes is not None
            else None
        )
        return cls(
            attempt_id=actual_attempt_id,
            session_id=definition.session_id,
            package_id=definition.package_id,
            candidate_name=definition.validate_candidate(candidate_name),
            machine_name=machine_name,
            windows_user=windows_user,
            software_version=software_version,
            status=AttemptStatus.ACTIVE,
            started_at=started_at,
            deadline_at=deadline,
            last_effective_at=started_at,
            displayed_questions=displayed,
        )

    def question_at(self, definition: ExamDefinition, index: int | None = None) -> SessionQuestion:
        selected_index = self.current_index if index is None else index
        displayed = self.displayed_questions[selected_index]
        by_id = {item.question_id: item for item in definition.questions}
        return by_id[displayed.question_id]

    def go_next(self) -> int:
        self.current_index = min(self.current_index + 1, len(self.displayed_questions) - 1)
        return self.current_index

    def go_previous(self) -> int:
        self.current_index = max(self.current_index - 1, 0)
        return self.current_index

    def jump_to(self, display_number: int) -> int:
        if not 1 <= display_number <= len(self.displayed_questions):
            raise IndexError(display_number)
        self.current_index = display_number - 1
        return self.current_index

    def set_response(self, question_id: str, response: object) -> None:
        if question_id not in {item.question_id for item in self.displayed_questions}:
            raise KeyError(question_id)
        if _response_is_empty(response):
            self.responses.pop(question_id, None)
        else:
            self.responses[question_id] = response

    def toggle_mark(self, question_id: str) -> bool:
        if question_id in self.marked_question_ids:
            self.marked_question_ids.remove(question_id)
            return False
        self.marked_question_ids.add(question_id)
        return True

    def unanswered_question_ids(self) -> list[str]:
        return [
            item.question_id
            for item in self.displayed_questions
            if _response_is_empty(self.responses.get(item.question_id))
        ]

    def submit(self, reason: SubmitReason, at: datetime) -> None:
        if self.status is not AttemptStatus.ACTIVE:
            raise ValueError("exam attempt is not active")
        self.status = AttemptStatus.SUBMITTED
        self.submit_reason = reason
        self.submitted_at = at.astimezone(UTC)

    def to_payload(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "session_id": self.session_id,
            "package_id": self.package_id,
            "candidate_name": self.candidate_name,
            "machine_name": self.machine_name,
            "windows_user": self.windows_user,
            "software_version": self.software_version,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "deadline_at": self.deadline_at.isoformat() if self.deadline_at else None,
            "last_effective_at": self.last_effective_at.isoformat(),
            "displayed_questions": [
                {
                    "question_id": item.question_id,
                    "question_version": item.question_version,
                    "display_order": item.display_order,
                    "option_order": list(item.option_order),
                }
                for item in self.displayed_questions
            ],
            "responses": self.responses,
            "marked_question_ids": sorted(self.marked_question_ids),
            "warnings_shown": sorted(self.warnings_shown),
            "current_index": self.current_index,
            "time_anomaly": self.time_anomaly,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "submit_reason": self.submit_reason.value if self.submit_reason else None,
        }

    @classmethod
    def from_payload(cls, payload: dict, definition: ExamDefinition) -> ExamState:
        if payload.get("session_id") != definition.session_id:
            raise ExamStateStoreError("saved exam belongs to another session")
        if payload.get("package_id") != definition.package_id:
            raise ExamStateStoreError("saved exam belongs to another package version")
        return cls(
            attempt_id=str(payload["attempt_id"]),
            session_id=str(payload["session_id"]),
            package_id=str(payload["package_id"]),
            candidate_name=str(payload["candidate_name"]),
            machine_name=str(payload["machine_name"]),
            windows_user=str(payload["windows_user"]),
            software_version=str(payload["software_version"]),
            status=AttemptStatus(payload["status"]),
            started_at=datetime.fromisoformat(payload["started_at"]),
            deadline_at=_optional_datetime(payload.get("deadline_at")),
            last_effective_at=datetime.fromisoformat(payload["last_effective_at"]),
            displayed_questions=[
                DisplayedQuestion(
                    str(item["question_id"]),
                    int(item["question_version"]),
                    int(item["display_order"]),
                    tuple(item.get("option_order", [])),
                )
                for item in payload["displayed_questions"]
            ],
            responses=dict(payload.get("responses", {})),
            marked_question_ids=set(payload.get("marked_question_ids", [])),
            warnings_shown={int(value) for value in payload.get("warnings_shown", [])},
            current_index=int(payload.get("current_index", 0)),
            time_anomaly=bool(payload.get("time_anomaly", False)),
            submitted_at=_optional_datetime(payload.get("submitted_at")),
            submit_reason=(SubmitReason(payload["submit_reason"]) if payload.get("submit_reason") else None),
        )


class ExamStateStore:
    def __init__(
        self,
        directories: list[Path],
        key: bytes,
        *,
        filename: str = "active_exam.state",
    ) -> None:
        if len(key) != 32:
            raise ValueError("exam state key must be 32 bytes")
        if not directories:
            raise ValueError("at least one state directory is required")
        if not filename or Path(filename).name != filename:
            raise ValueError("exam state filename must be a plain file name")
        self.directories = [directory.resolve() for directory in directories]
        self.key = key
        self.filename = filename

    @property
    def paths(self) -> list[Path]:
        return [directory / self.filename for directory in self.directories]

    def save(self, state: ExamState) -> None:
        payload = json.dumps(
            state.to_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = secrets.token_bytes(12)
        encrypted = STATE_MAGIC + nonce + AESGCM(self.key).encrypt(nonce, payload, STATE_MAGIC)
        errors = []
        for path in self.paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, encrypted)
            except OSError as exc:
                errors.append(exc)
        if len(errors) == len(self.paths):
            raise ExamStateStoreError("all exam state locations failed") from errors[0]

    def load(self, definition: ExamDefinition) -> ExamState:
        errors = []
        for path in self.paths:
            if not path.exists():
                continue
            try:
                data = path.read_bytes()
                if len(data) < 36 or not data.startswith(STATE_MAGIC):
                    raise ExamStateStoreError("saved exam file type is invalid")
                nonce = data[8:20]
                payload = AESGCM(self.key).decrypt(nonce, data[20:], STATE_MAGIC)
                return ExamState.from_payload(json.loads(payload.decode("utf-8")), definition)
            except (InvalidTag, OSError, UnicodeDecodeError, json.JSONDecodeError, ExamStateStoreError) as exc:
                errors.append(exc)
        if errors:
            raise ExamStateStoreError("saved exam state is damaged") from errors[0]
        raise FileNotFoundError("no active exam state")

    def clear(self) -> None:
        for path in self.paths:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


def _response_is_empty(response: object) -> bool:
    if response is None:
        return True
    if isinstance(response, str):
        return not response.strip()
    if isinstance(response, (list, tuple, set)):
        return not response or all(not str(value).strip() for value in response)
    return False


def _optional_datetime(value) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="exam-state-", dir=path.parent)
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
