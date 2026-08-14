from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

from .enums import MatchMode, QuestionStatus, QuestionType, UsageScope


def new_id() -> str:
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class QuestionOption:
    key: str
    text: str
    asset_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BlankDefinition:
    index: int
    accepted_answers: tuple[str, ...]
    score: Decimal
    match_mode: MatchMode = MatchMode.TEXT_SIMILARITY


@dataclass(frozen=True, slots=True)
class UnorderedGroup:
    indexes: tuple[int, ...]


@dataclass(slots=True)
class QuestionDraft:
    question_type: QuestionType
    stem: str
    basis: str
    display_number: str = ""
    status: QuestionStatus = QuestionStatus.DRAFT
    usage_scope: UsageScope = UsageScope.BOTH
    applicable_year: int | None = None
    source: str = ""
    chapter: str = ""
    clause: str = ""
    difficulty: str = ""
    tags: list[str] = field(default_factory=list)
    options: list[QuestionOption] = field(default_factory=list)
    correct_option_keys: set[str] = field(default_factory=set)
    blanks: list[BlankDefinition] = field(default_factory=list)
    unordered_groups: list[UnorderedGroup] = field(default_factory=list)
    question_asset_ids: list[str] = field(default_factory=list)
    score: Decimal = Decimal("0")
    id: str = field(default_factory=new_id)

