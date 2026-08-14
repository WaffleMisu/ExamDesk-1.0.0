from __future__ import annotations

from dataclasses import dataclass, field

from examdesk.domain.models import QuestionDraft


@dataclass(frozen=True, slots=True)
class ImportIssue:
    row: int
    severity: str
    code: str
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class PendingImage:
    owner_key: str
    data: bytes
    filename_hint: str


@dataclass(slots=True)
class ImportCandidate:
    source_location: str
    question: QuestionDraft
    images: list[PendingImage] = field(default_factory=list)
    provided_fields: frozenset[str] = frozenset()


@dataclass(slots=True)
class ImportPreview:
    source_kind: str
    candidates: list[ImportCandidate]
    issues: list[ImportIssue]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)
