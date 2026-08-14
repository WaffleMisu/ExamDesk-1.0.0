from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from examdesk.domain.enums import QuestionStatus, QuestionType, ReviewPolicy, SessionStatus, UsageScope
from examdesk.domain.models import QuestionDraft
from examdesk.scoring import SimilarityLevel


@dataclass(frozen=True, slots=True)
class SessionFilter:
    applicable_year: int | None = None
    chapters: frozenset[str] = frozenset()
    difficulties: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()

    def matches(self, question: QuestionDraft) -> bool:
        if question.status is not QuestionStatus.ENABLED:
            return False
        if question.usage_scope not in (UsageScope.EXAM_ONLY, UsageScope.BOTH):
            return False
        if self.applicable_year is not None and question.applicable_year not in (
            None,
            self.applicable_year,
        ):
            return False
        if self.chapters and question.chapter not in self.chapters:
            return False
        if self.difficulties and question.difficulty not in self.difficulties:
            return False
        return not (self.tags and not self.tags.intersection(question.tags))

    def to_payload(self) -> dict:
        return {
            "applicable_year": self.applicable_year,
            "chapters": sorted(self.chapters),
            "difficulties": sorted(self.difficulties),
            "tags": sorted(self.tags),
        }

    @classmethod
    def from_payload(cls, payload: dict) -> SessionFilter:
        return cls(
            applicable_year=payload.get("applicable_year"),
            chapters=frozenset(payload.get("chapters", [])),
            difficulties=frozenset(payload.get("difficulties", [])),
            tags=frozenset(payload.get("tags", [])),
        )


@dataclass(frozen=True, slots=True)
class RosterEntry:
    display_name: str
    department: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionQuestion:
    question_id: str
    question_version: int
    base_order: int
    question: QuestionDraft


@dataclass(frozen=True, slots=True)
class SessionDraft:
    id: str
    name: str
    description: str
    status: SessionStatus
    session_filter: SessionFilter
    question_counts: dict[QuestionType, int]
    max_attempts: int
    roster_required: bool
    duration_minutes: int | None
    review_policy: ReviewPolicy
    review_release_at: datetime | None
    min_software_version: str
    random_seed: str
    questions: tuple[SessionQuestion, ...]
    roster: tuple[RosterEntry, ...]
    monitoring_enabled: bool = False
    similarity_level: SimilarityLevel = SimilarityLevel.STANDARD
    custom_similarity_threshold: float | None = None

    @property
    def max_score(self) -> Decimal:
        return sum((item.question.score for item in self.questions), Decimal("0"))


@dataclass(frozen=True, slots=True)
class ExamDefinition:
    session_id: str
    package_id: str
    name: str
    description: str
    max_attempts: int
    duration_minutes: int | None
    review_policy: ReviewPolicy
    review_release_at: datetime | None
    min_software_version: str
    random_seed: str
    session_auth_key: bytes
    result_recipient_public_key: bytes
    questions: tuple[SessionQuestion, ...]
    roster: tuple[RosterEntry, ...]
    roster_required: bool
    assets: dict[str, bytes]
    monitoring_enabled: bool = False
    similarity_level: SimilarityLevel = SimilarityLevel.STANDARD
    custom_similarity_threshold: float | None = None

    @property
    def max_score(self) -> Decimal:
        return sum((item.question.score for item in self.questions), Decimal("0"))

    def validate_candidate(self, name: str) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("姓名不能为空")
        if self.roster_required:
            names = {entry.display_name.casefold(): entry.display_name for entry in self.roster}
            matched = names.get(cleaned.casefold())
            if matched is None:
                raise ValueError("姓名不在本场考试名单中")
            return matched
        return cleaned
