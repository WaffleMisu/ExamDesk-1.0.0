from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from examdesk.exam import ExamGrade


@dataclass(frozen=True, slots=True)
class ResultArtifact:
    attempt_id: str
    local_backup: Path
    local_review: Path
    submission_file: Path | None
    grade: ExamGrade
    submission_error: str | None = None


@dataclass(frozen=True, slots=True)
class ImportedResult:
    source_path: Path
    attempt_id: str | None
    candidate_name: str | None
    imported: bool
    duplicate_file: bool = False
    duplicate_candidate: bool = False
    error: str | None = None


@dataclass(slots=True)
class BatchImportResult:
    items: list[ImportedResult] = field(default_factory=list)

    @property
    def imported_count(self) -> int:
        return sum(item.imported for item in self.items)

    @property
    def error_count(self) -> int:
        return sum(item.error is not None for item in self.items)
