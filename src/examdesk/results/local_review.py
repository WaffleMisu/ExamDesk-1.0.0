from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from examdesk.domain.enums import ReviewPolicy
from examdesk.sessions import ExamDefinition

REVIEW_MAGIC = b"EXDKRV10"
REVIEW_SUFFIX = ".examreview"


class SubmittedReviewError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SubmittedReview:
    attempt_id: str
    candidate_name: str
    submitted_at: datetime
    strict_score: Decimal
    estimated_score: Decimal
    max_score: Decimal
    policy: ReviewPolicy
    release_at: datetime | None
    details_visible: bool
    questions: tuple[dict, ...]


class SubmittedReviewStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def save(self, definition: ExamDefinition, payload: dict) -> Path:
        _validate_payload(definition, payload)
        candidate_name = definition.validate_candidate(str(payload["candidate_name"]))
        document = {
            "schema_version": 1,
            "review_policy": definition.review_policy.value,
            "review_release_at": (
                definition.review_release_at.isoformat() if definition.review_release_at else None
            ),
            "result": payload,
        }
        plaintext = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = secrets.token_bytes(12)
        key = _derive_key(definition)
        encrypted = REVIEW_MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, REVIEW_MAGIC)
        prefix = _candidate_prefix(definition.package_id, candidate_name)
        path = self.directory / f"{prefix}_{payload['attempt_id']}{REVIEW_SUFFIX}"
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, encrypted)
        return path

    def load_latest(
        self,
        definition: ExamDefinition,
        candidate_name: str,
        *,
        now: datetime | None = None,
    ) -> SubmittedReview:
        cleaned_name = definition.validate_candidate(candidate_name)
        prefix = _candidate_prefix(definition.package_id, cleaned_name)
        paths = sorted(self.directory.glob(f"{prefix}_*{REVIEW_SUFFIX}"))
        if not paths:
            raise FileNotFoundError("没有找到该考生在本机的已交卷记录")

        records = []
        errors = []
        for path in paths:
            try:
                records.append(self._load_file(definition, cleaned_name, path, now=now))
            except (OSError, InvalidTag, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(exc)
        if not records:
            raise SubmittedReviewError("本机已交卷记录损坏或不属于当前场次") from errors[0]
        return max(records, key=lambda item: item.submitted_at)

    def _load_file(
        self,
        definition: ExamDefinition,
        candidate_name: str,
        path: Path,
        *,
        now: datetime | None,
    ) -> SubmittedReview:
        data = path.read_bytes()
        if len(data) < 36 or not data.startswith(REVIEW_MAGIC):
            raise SubmittedReviewError("本机已交卷记录格式无效")
        nonce = data[8:20]
        plaintext = AESGCM(_derive_key(definition)).decrypt(
            nonce,
            data[20:],
            REVIEW_MAGIC,
        )
        document = json.loads(plaintext.decode("utf-8"))
        if document.get("schema_version") != 1:
            raise SubmittedReviewError("不支持的本机已交卷记录版本")
        payload = document["result"]
        _validate_payload(definition, payload)
        if str(payload["candidate_name"]).casefold() != candidate_name.casefold():
            raise SubmittedReviewError("本机已交卷记录不属于该考生")

        policy = definition.review_policy
        release_at = definition.review_release_at
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        details_visible = policy is ReviewPolicy.IMMEDIATE or (
            policy is ReviewPolicy.AFTER_RELEASE
            and release_at is not None
            and current_time >= release_at.astimezone(UTC)
        )
        return SubmittedReview(
            attempt_id=str(payload["attempt_id"]),
            candidate_name=str(payload["candidate_name"]),
            submitted_at=datetime.fromisoformat(payload["submitted_at"]),
            strict_score=Decimal(str(payload["strict_score"])),
            estimated_score=Decimal(str(payload["estimated_score"])),
            max_score=Decimal(str(payload["max_score"])),
            policy=policy,
            release_at=release_at,
            details_visible=details_visible,
            questions=tuple(payload["questions"]) if details_visible else (),
        )


def _validate_payload(definition: ExamDefinition, payload: dict) -> None:
    if str(payload.get("session_id")) != definition.session_id:
        raise SubmittedReviewError("本机已交卷记录不属于当前场次")
    if str(payload.get("package_id")) != definition.package_id:
        raise SubmittedReviewError("本机已交卷记录不属于当前场次包版本")


def _candidate_prefix(package_id: str, candidate_name: str) -> str:
    value = f"{package_id}\0{candidate_name.casefold()}".encode()
    return hashlib.sha256(value).hexdigest()[:24]


def _derive_key(definition: ExamDefinition) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=definition.package_id.encode("utf-8"),
        info=b"examdesk-submitted-review-v1",
    ).derive(definition.session_auth_key)


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="submitted-review-", dir=path.parent)
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
