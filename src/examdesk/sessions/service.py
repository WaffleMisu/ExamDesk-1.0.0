from __future__ import annotations

import base64
import json
import random
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

from examdesk.domain.enums import QuestionType, ReviewPolicy, SessionStatus
from examdesk.packages import (
    PackageError,
    PasswordPackageCodec,
    SigningKeyPair,
    X25519KeyPair,
    build_archive,
    read_archive,
)
from examdesk.questions import AssetManager, QuestionRepository, question_from_payload, question_to_payload
from examdesk.scoring import SimilarityLevel, validate_similarity_settings
from examdesk.security.passwords import hash_secret, verify_secret

from .models import ExamDefinition, RosterEntry, SessionDraft, SessionFilter, SessionQuestion


class SessionError(ValueError):
    pass


class SessionService:
    def __init__(self, database, questions: QuestionRepository, assets: AssetManager) -> None:
        self.database = database
        self.questions = questions
        self.assets = assets

    def create_draft(
        self,
        *,
        name: str,
        description: str,
        password: str,
        session_filter: SessionFilter,
        question_counts: dict[QuestionType, int],
        max_attempts: int,
        roster: list[RosterEntry],
        roster_required: bool,
        duration_minutes: int | None,
        review_policy: ReviewPolicy,
        review_release_at: datetime | None,
        min_software_version: str,
        created_by: str,
        monitoring_enabled: bool = False,
        similarity_level: SimilarityLevel = SimilarityLevel.STANDARD,
        custom_similarity_threshold: float | None = None,
    ) -> SessionDraft:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise SessionError("场次名称不能为空")
        if max_attempts < 1:
            raise SessionError("最大答题次数必须大于0")
        if duration_minutes is not None and duration_minutes <= 0:
            raise SessionError("限时时长必须大于0")
        if review_policy is ReviewPolicy.AFTER_RELEASE and review_release_at is None:
            raise SessionError("指定时间后查看答案必须设置开放时间")
        if roster_required and not roster:
            raise SessionError("启用考生名单时名单不能为空")
        _validate_roster(roster)
        try:
            Version(min_software_version)
        except InvalidVersion as exc:
            raise SessionError("最低软件版本格式无效") from exc
        try:
            validate_similarity_settings(similarity_level, custom_similarity_threshold)
        except ValueError as exc:
            raise SessionError("填空相似度设置无效") from exc

        normalized_counts = {
            question_type: int(question_counts.get(question_type, 0))
            for question_type in QuestionType
        }
        if any(value < 0 for value in normalized_counts.values()) or sum(normalized_counts.values()) <= 0:
            raise SessionError("各题型数量不能为负且合计必须大于0")
        if password:
            self._ensure_password_unique(password)
        selected = self._select_questions(session_filter, normalized_counts)
        session_id = str(uuid4())
        random_seed = secrets.token_hex(24)
        random.Random(random_seed).shuffle(selected)
        now = datetime.now(UTC).isoformat()

        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    id, name, description, status, password_digest, filters_json,
                    question_counts_json, max_attempts, roster_required, duration_minutes,
                    review_policy, review_release_at, min_software_version, created_by,
                    created_at, random_seed, monitoring_enabled, similarity_level,
                    custom_similarity_threshold
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    cleaned_name,
                    description.strip(),
                    hash_secret(password).encode() if password else "",
                    _json(session_filter.to_payload()),
                    _json({key.value: value for key, value in normalized_counts.items()}),
                    max_attempts,
                    int(roster_required),
                    duration_minutes,
                    review_policy.value,
                    review_release_at.isoformat() if review_release_at else None,
                    min_software_version,
                    created_by,
                    now,
                    random_seed,
                    int(monitoring_enabled),
                    similarity_level.value,
                    custom_similarity_threshold,
                ),
            )
            for order, (question, version) in enumerate(selected, start=1):
                connection.execute(
                    """
                    INSERT INTO session_questions(
                        session_id, question_id, question_version, base_order, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, question.id, version, order, _json(question_to_payload(question))),
                )
            connection.executemany(
                """
                INSERT INTO session_roster(session_id, display_name, department, extra_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (session_id, item.display_name.strip(), item.department.strip(), _json(item.extra))
                    for item in roster
                ],
            )
        return self.get(session_id)

    def replace_question(self, session_id: str, old_question_id: str, new_question_id: str) -> SessionDraft:
        session = self.get(session_id)
        if session.status is not SessionStatus.DRAFT:
            raise SessionError("锁定后的场次不能替换题目")
        old = next((item for item in session.questions if item.question_id == old_question_id), None)
        if old is None:
            raise SessionError("待替换题目不在场次中")
        if any(item.question_id == new_question_id for item in session.questions):
            raise SessionError("新题目已在场次中")
        new_question = self.questions.get(new_question_id)
        if new_question.question_type is not old.question.question_type:
            raise SessionError("替换题必须与原题题型一致")
        if not session.session_filter.matches(new_question):
            raise SessionError("替换题不符合场次筛选条件")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT current_version FROM questions WHERE id = ?",
                (new_question_id,),
            ).fetchone()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE session_questions
                SET question_id = ?, question_version = ?, snapshot_json = ?
                WHERE session_id = ? AND question_id = ?
                """,
                (
                    new_question_id,
                    int(row["current_version"]),
                    _json(question_to_payload(new_question)),
                    session_id,
                    old_question_id,
                ),
            )
        return self.get(session_id)

    def lock(self, session_id: str) -> SessionDraft:
        session = self.get(session_id)
        if session.status is SessionStatus.LOCKED:
            return session
        if session.status is not SessionStatus.DRAFT:
            raise SessionError("只有草稿场次可以锁定")
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE sessions SET status = 'locked', locked_at = ?,
                    session_auth_key = ?, package_id = ? WHERE id = ?
                """,
                (datetime.now(UTC).isoformat(), secrets.token_bytes(32), str(uuid4()), session_id),
            )
        return self.get(session_id)

    def export_package(
        self,
        session_id: str,
        *,
        password: str,
        signer: SigningKeyPair,
        result_recipient: X25519KeyPair,
    ) -> bytes:
        session = self.get(session_id)
        if session.status is not SessionStatus.LOCKED:
            raise SessionError("场次必须锁定后才能导出")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT password_digest, session_auth_key, package_id FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        stored_password = row["password_digest"] or ""
        if stored_password and not verify_secret(password, stored_password):
            raise SessionError("场次密码错误")
        if not stored_password and password:
            raise SessionError("该场次未设置密码")
        question_payloads, asset_files = self._package_questions(session)
        manifest = {
            "kind": "exam",
            "schema_version": 1,
            "session_id": session.id,
            "package_id": row["package_id"],
            "name": session.name,
            "description": session.description,
            "max_attempts": session.max_attempts,
            "roster_required": session.roster_required,
            "monitoring_enabled": session.monitoring_enabled,
            "duration_minutes": session.duration_minutes,
            "review_policy": session.review_policy.value,
            "review_release_at": session.review_release_at.isoformat() if session.review_release_at else None,
            "min_software_version": session.min_software_version,
            "random_seed": session.random_seed,
            "similarity_level": session.similarity_level.value,
            "custom_similarity_threshold": session.custom_similarity_threshold,
            "session_auth_key": _b64(row["session_auth_key"]),
            "result_recipient_public_key": _b64(result_recipient.public_bytes),
            "questions": question_payloads,
            "roster": [
                {"display_name": item.display_name, "department": item.department, "extra": item.extra}
                for item in session.roster
            ],
        }
        return PasswordPackageCodec.encode(
            build_archive(manifest, asset_files),
            package_kind="exam",
            password=password,
            signer=signer,
            minimum_software_version=session.min_software_version,
            package_id=row["package_id"],
        )

    def password_required(self, session_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT password_digest FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return bool(row["password_digest"])

    def available_question_counts(
        self,
        session_filter: SessionFilter,
    ) -> dict[QuestionType, int]:
        available = self._available_questions(session_filter)
        return {
            question_type: len(available[question_type])
            for question_type in QuestionType
        }

    def get(self, session_id: str) -> SessionDraft:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            question_rows = connection.execute(
                "SELECT * FROM session_questions WHERE session_id = ? ORDER BY base_order",
                (session_id,),
            ).fetchall()
            roster_rows = connection.execute(
                "SELECT * FROM session_roster WHERE session_id = ? ORDER BY display_name",
                (session_id,),
            ).fetchall()
        return SessionDraft(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            status=SessionStatus(row["status"]),
            session_filter=SessionFilter.from_payload(json.loads(row["filters_json"])),
            question_counts={
                QuestionType(key): int(value) for key, value in json.loads(row["question_counts_json"]).items()
            },
            max_attempts=row["max_attempts"],
            roster_required=bool(row["roster_required"]),
            duration_minutes=row["duration_minutes"],
            review_policy=ReviewPolicy(row["review_policy"]),
            review_release_at=_optional_datetime(row["review_release_at"]),
            min_software_version=row["min_software_version"],
            random_seed=row["random_seed"],
            questions=tuple(
                SessionQuestion(
                    question_id=item["question_id"],
                    question_version=item["question_version"],
                    base_order=item["base_order"],
                    question=question_from_payload(json.loads(item["snapshot_json"])),
                )
                for item in question_rows
            ),
            roster=tuple(
                RosterEntry(item["display_name"], item["department"], json.loads(item["extra_json"]))
                for item in roster_rows
            ),
            monitoring_enabled=bool(row["monitoring_enabled"]),
            similarity_level=SimilarityLevel(row["similarity_level"]),
            custom_similarity_threshold=row["custom_similarity_threshold"],
        )

    def _select_questions(self, session_filter: SessionFilter, counts: dict[QuestionType, int]):
        available = self._available_questions(session_filter)
        selected = []
        system_random = secrets.SystemRandom()
        for question_type, count in counts.items():
            candidates = available[question_type]
            if len(candidates) < count:
                raise SessionError(
                    f"{question_type.value}题数量不足：需要{count}道，符合条件的只有{len(candidates)}道"
                )
            selected.extend(system_random.sample(candidates, count))
        return selected

    def _available_questions(self, session_filter: SessionFilter):
        available: dict[QuestionType, list] = {question_type: [] for question_type in QuestionType}
        for question, version in self.questions.list_current():
            if session_filter.matches(question):
                available[question.question_type].append((question, version))
        return available

    def _ensure_password_unique(self, password: str) -> None:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT password_digest FROM sessions WHERE status != 'archived'"
            ).fetchall()
        if any(verify_secret(password, row["password_digest"]) for row in rows):
            raise SessionError("该密码已被其他未归档场次使用")

    def _package_questions(self, session: SessionDraft):
        payloads = []
        files: dict[str, bytes] = {}
        for item in session.questions:
            question = self.questions.get(item.question_id, item.question_version)
            asset_ids = list(question.question_asset_ids)
            asset_ids.extend(asset_id for option in question.options for asset_id in option.asset_ids)
            asset_sha_by_id = {}
            for asset_id in asset_ids:
                asset = self.assets.get(asset_id)
                asset_sha_by_id[asset_id] = asset.sha256
                files.setdefault(asset.sha256, self.assets.absolute_path(asset).read_bytes())
            payloads.append(
                {
                    "question_id": item.question_id,
                    "question_version": item.question_version,
                    "base_order": item.base_order,
                    "question": question_to_payload(question, asset_sha_by_id),
                }
            )
        return payloads, files


class ExamPackageReader:
    @staticmethod
    def open(
        package: bytes,
        *,
        password: str,
        trusted_signers: dict[str, Ed25519PublicKey],
        current_software_version: str,
    ) -> ExamDefinition:
        decoded = PasswordPackageCodec.decode(
            package,
            password=password,
            trusted_signers=trusted_signers,
            expected_kind="exam",
        )
        archive = read_archive(decoded.payload)
        manifest = archive.manifest
        if manifest.get("kind") != "exam" or manifest.get("schema_version") != 1:
            raise PackageError("unsupported exam package manifest")
        try:
            if Version(current_software_version) < Version(str(manifest["min_software_version"])):
                raise SessionError(f"软件版本过低，需要{manifest['min_software_version']}或更高版本")
        except InvalidVersion as exc:
            raise SessionError("场次包中的软件版本格式无效") from exc
        questions = tuple(
            SessionQuestion(
                question_id=str(item["question_id"]),
                question_version=int(item["question_version"]),
                base_order=int(item["base_order"]),
                question=question_from_payload(item["question"]),
            )
            for item in manifest["questions"]
        )
        roster = tuple(
            RosterEntry(str(item["display_name"]), str(item.get("department", "")), item.get("extra", {}))
            for item in manifest.get("roster", [])
        )
        try:
            similarity_level = SimilarityLevel(
                str(manifest.get("similarity_level", SimilarityLevel.STANDARD.value))
            )
            custom_similarity_threshold = manifest.get("custom_similarity_threshold")
            if custom_similarity_threshold is not None:
                custom_similarity_threshold = float(custom_similarity_threshold)
            validate_similarity_settings(similarity_level, custom_similarity_threshold)
        except (TypeError, ValueError) as exc:
            raise PackageError("exam package similarity settings are invalid") from exc
        return ExamDefinition(
            session_id=str(manifest["session_id"]),
            package_id=str(manifest["package_id"]),
            name=str(manifest["name"]),
            description=str(manifest.get("description", "")),
            max_attempts=int(manifest["max_attempts"]),
            duration_minutes=manifest.get("duration_minutes"),
            review_policy=ReviewPolicy(manifest["review_policy"]),
            review_release_at=_optional_datetime(manifest.get("review_release_at")),
            min_software_version=str(manifest["min_software_version"]),
            random_seed=str(manifest["random_seed"]),
            session_auth_key=_unb64(manifest["session_auth_key"]),
            result_recipient_public_key=_unb64(manifest["result_recipient_public_key"]),
            questions=questions,
            roster=roster,
            roster_required=bool(manifest["roster_required"]),
            assets=archive.assets,
            monitoring_enabled=bool(manifest["monitoring_enabled"]),
            similarity_level=similarity_level,
            custom_similarity_threshold=custom_similarity_threshold,
        )


def _validate_roster(roster: list[RosterEntry]) -> None:
    names = [item.display_name.strip().casefold() for item in roster]
    if any(not name for name in names):
        raise SessionError("考生名单中姓名不能为空")
    if len(names) != len(set(names)):
        raise SessionError("考生名单中存在重复姓名，请先增加区分信息")


def _optional_datetime(value) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PackageError("exam package key is invalid") from exc
