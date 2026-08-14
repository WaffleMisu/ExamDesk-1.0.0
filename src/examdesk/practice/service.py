from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from examdesk.domain.enums import QuestionStatus, QuestionType, UsageScope
from examdesk.domain.models import QuestionDraft
from examdesk.packages import PasswordPackageCodec, SigningKeyPair, build_archive, read_archive
from examdesk.questions import AssetManager, QuestionRepository, question_from_payload, question_to_payload
from examdesk.scoring import (
    SimilarityLevel,
    grade_choice,
    grade_fill,
    validate_similarity_settings,
)


@dataclass(frozen=True, slots=True)
class PracticeFilter:
    applicable_year: int | None = None
    chapters: frozenset[str] = frozenset()
    question_types: frozenset[QuestionType] = frozenset()
    tags: frozenset[str] = frozenset()

    def matches(self, question: QuestionDraft) -> bool:
        if question.status is not QuestionStatus.ENABLED:
            return False
        if question.usage_scope not in (UsageScope.PRACTICE_ONLY, UsageScope.BOTH):
            return False
        if self.applicable_year is not None and question.applicable_year not in (
            None,
            self.applicable_year,
        ):
            return False
        if self.chapters and question.chapter not in self.chapters:
            return False
        if self.question_types and question.question_type not in self.question_types:
            return False
        return not (self.tags and not self.tags.intersection(question.tags))


@dataclass(frozen=True, slots=True)
class PracticeDefinition:
    bank_id: str
    package_id: str
    name: str
    bank_revision: int
    questions: tuple[QuestionDraft, ...]
    assets: dict[str, bytes]
    similarity_level: SimilarityLevel = SimilarityLevel.STANDARD
    custom_similarity_threshold: float | None = None


@dataclass(frozen=True, slots=True)
class PracticeQuestionGrade:
    question_id: str
    strict_score: Decimal
    estimated_score: Decimal
    max_score: Decimal

    @property
    def is_wrong(self) -> bool:
        return self.strict_score < self.max_score


@dataclass(frozen=True, slots=True)
class PracticeGrade:
    strict_score: Decimal
    estimated_score: Decimal
    max_score: Decimal
    questions: tuple[PracticeQuestionGrade, ...]

    @property
    def wrong_question_ids(self) -> tuple[str, ...]:
        return tuple(item.question_id for item in self.questions if item.is_wrong)


@dataclass(slots=True)
class PracticeSession:
    definition: PracticeDefinition
    questions: list[QuestionDraft]
    responses: dict[str, object] = field(default_factory=dict)

    def set_response(self, question_id: str, response: object) -> None:
        if question_id not in {question.id for question in self.questions}:
            raise KeyError(question_id)
        self.responses[question_id] = response

    def grade_question(
        self,
        question_id: str,
        similarity_level: SimilarityLevel | None = None,
        custom_similarity_threshold: float | None = None,
    ) -> PracticeQuestionGrade:
        question = next((item for item in self.questions if item.id == question_id), None)
        if question is None:
            raise KeyError(question_id)
        if similarity_level is None:
            similarity_level = self.definition.similarity_level
            custom_similarity_threshold = self.definition.custom_similarity_threshold
        response = self.responses.get(question.id)
        if question.question_type is QuestionType.FILL:
            values = list(response) if isinstance(response, (list, tuple)) else []
            result = grade_fill(
                values,
                question.blanks,
                question.unordered_groups,
                similarity_level,
                custom_similarity_threshold,
            )
            return PracticeQuestionGrade(
                question.id,
                result.strict_score,
                result.estimated_score,
                result.max_score,
            )

        selected = set(response) if isinstance(response, (list, tuple, set)) else set()
        result = grade_choice(
            question.question_type,
            selected,
            question.correct_option_keys,
            question.score,
        )
        return PracticeQuestionGrade(
            question.id,
            result.score,
            result.score,
            result.max_score,
        )

    def grade(
        self,
        similarity_level: SimilarityLevel | None = None,
        custom_similarity_threshold: float | None = None,
    ) -> PracticeGrade:
        if similarity_level is None:
            similarity_level = self.definition.similarity_level
            custom_similarity_threshold = self.definition.custom_similarity_threshold
        grades = [
            self.grade_question(question.id, similarity_level, custom_similarity_threshold)
            for question in self.questions
        ]
        return PracticeGrade(
            strict_score=sum((item.strict_score for item in grades), Decimal("0")),
            estimated_score=sum((item.estimated_score for item in grades), Decimal("0")),
            max_score=sum((item.max_score for item in grades), Decimal("0")),
            questions=tuple(grades),
        )


class PracticeService:
    def __init__(self, database, questions: QuestionRepository, assets: AssetManager) -> None:
        self.database = database
        self.questions = questions
        self.assets = assets

    def export_package(
        self,
        *,
        name: str,
        practice_filter: PracticeFilter,
        distribution_password: str,
        signer: SigningKeyPair,
        minimum_software_version: str,
        similarity_level: SimilarityLevel = SimilarityLevel.STANDARD,
        custom_similarity_threshold: float | None = None,
    ) -> bytes:
        validate_similarity_settings(similarity_level, custom_similarity_threshold)
        selected = [
            (question, version)
            for question, version in self.questions.list_current()
            if practice_filter.matches(question)
        ]
        if not selected:
            raise ValueError("没有符合条件的练习题")
        package_id = str(uuid4())
        bank_id = self._bank_id()
        payloads = []
        files = {}
        for question, version in selected:
            asset_ids = list(question.question_asset_ids)
            asset_ids.extend(asset_id for option in question.options for asset_id in option.asset_ids)
            asset_map = {}
            for asset_id in asset_ids:
                asset = self.assets.get(asset_id)
                asset_map[asset_id] = asset.sha256
                files.setdefault(asset.sha256, self.assets.absolute_path(asset).read_bytes())
            payloads.append({"version": version, "question": question_to_payload(question, asset_map)})
        manifest = {
            "kind": "practice",
            "schema_version": 1,
            "bank_id": bank_id,
            "package_id": package_id,
            "name": name.strip() or "练习题库",
            "bank_revision": self.questions.bank_revision(),
            "similarity_level": similarity_level.value,
            "custom_similarity_threshold": custom_similarity_threshold,
            "questions": payloads,
        }
        return PasswordPackageCodec.encode(
            build_archive(manifest, files),
            package_kind="practice",
            password=distribution_password,
            signer=signer,
            minimum_software_version=minimum_software_version,
            package_id=package_id,
        )

    @staticmethod
    def start_session(
        definition: PracticeDefinition,
        counts: dict[QuestionType, int],
        *,
        question_ids: set[str] | None = None,
    ) -> PracticeSession:
        pool = [
            question
            for question in definition.questions
            if question_ids is None or question.id in question_ids
        ]
        selected = []
        rng = secrets.SystemRandom()
        for question_type in QuestionType:
            count = int(counts.get(question_type, 0))
            candidates = [question for question in pool if question.question_type is question_type]
            if count < 0 or count > len(candidates):
                raise ValueError(f"{question_type.value}练习题数量不足")
            selected.extend(rng.sample(candidates, count))
        if not selected:
            raise ValueError("练习题数量合计必须大于0")
        rng.shuffle(selected)
        return PracticeSession(definition, selected)

    def save_progress(self, definition: PracticeDefinition, grade: PracticeGrade) -> None:
        wrong_ids = set(grade.wrong_question_ids)
        with self.database.transaction(immediate=True) as connection:
            for item in grade.questions:
                connection.execute(
                    """
                    INSERT INTO practice_progress(
                        bank_id, question_id, attempt_count, wrong_count, last_answered_at
                    ) VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(bank_id, question_id) DO UPDATE SET
                        attempt_count = attempt_count + 1,
                        wrong_count = wrong_count + excluded.wrong_count,
                        last_answered_at = CURRENT_TIMESTAMP
                    """,
                    (definition.bank_id, item.question_id, int(item.question_id in wrong_ids)),
                )

    def wrong_question_ids(self, bank_id: str) -> set[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT question_id FROM practice_progress WHERE bank_id = ? AND wrong_count > 0",
                (bank_id,),
            ).fetchall()
        return {row["question_id"] for row in rows}

    def _bank_id(self) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = 'bank_id'"
            ).fetchone()
        if row is not None:
            return str(json.loads(row["value_json"]))
        bank_id = str(uuid4())
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value_json, updated_at) VALUES ('bank_id', ?, CURRENT_TIMESTAMP)",
                (json.dumps(bank_id),),
            )
        return bank_id


class PracticePackageReader:
    @staticmethod
    def open(
        package: bytes,
        *,
        distribution_password: str,
        trusted_signers: dict[str, Ed25519PublicKey],
    ) -> PracticeDefinition:
        decoded = PasswordPackageCodec.decode(
            package,
            password=distribution_password,
            trusted_signers=trusted_signers,
            expected_kind="practice",
        )
        archive = read_archive(decoded.payload)
        manifest = archive.manifest
        if manifest.get("kind") != "practice" or manifest.get("schema_version") != 1:
            raise ValueError("练习包格式不受支持")
        questions = tuple(question_from_payload(item["question"]) for item in manifest["questions"])
        try:
            similarity_level = SimilarityLevel(
                str(manifest.get("similarity_level", SimilarityLevel.STANDARD.value))
            )
            custom_similarity_threshold = manifest.get("custom_similarity_threshold")
            if custom_similarity_threshold is not None:
                custom_similarity_threshold = float(custom_similarity_threshold)
            validate_similarity_settings(similarity_level, custom_similarity_threshold)
        except (TypeError, ValueError) as exc:
            raise ValueError("练习包中的填空相似度设置无效") from exc
        return PracticeDefinition(
            bank_id=str(manifest["bank_id"]),
            package_id=str(manifest["package_id"]),
            name=str(manifest["name"]),
            bank_revision=int(manifest["bank_revision"]),
            questions=questions,
            assets=archive.assets,
            similarity_level=similarity_level,
            custom_similarity_threshold=custom_similarity_threshold,
        )
