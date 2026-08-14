from __future__ import annotations

import json
from dataclasses import dataclass

from rapidfuzz.fuzz import ratio

from examdesk.domain.models import QuestionDraft

from .repository import QuestionRepository, _duplicate_normalize


@dataclass(frozen=True, slots=True)
class SimilarQuestion:
    question_id: str
    version: int
    display_number: str
    stem: str
    similarity: float
    is_exact: bool


class DuplicateChecker:
    def __init__(self, database) -> None:
        self.database = database
        self.repository = QuestionRepository(database)

    def find_similar(
        self,
        draft: QuestionDraft,
        *,
        minimum_similarity: float = 78.0,
        limit: int = 20,
    ) -> list[SimilarQuestion]:
        target_stem = _duplicate_normalize(draft.stem)
        target_options = _options_text(draft.options)
        target_key = self.repository.fingerprints(draft).content
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT q.id, q.display_number, q.current_version,
                       qv.stem, qv.options_json, qv.content_hash
                FROM questions q
                JOIN question_versions qv
                  ON qv.question_id = q.id AND qv.version = q.current_version
                WHERE q.question_type = ?
                """,
                (draft.question_type.value,),
            ).fetchall()

        matches = []
        for row in rows:
            stem_similarity = float(ratio(target_stem, _duplicate_normalize(row["stem"])))
            existing_options = _options_text_from_json(row["options_json"])
            option_similarity = (
                float(ratio(target_options, existing_options))
                if target_options or existing_options
                else 100.0
            )
            combined = stem_similarity * 0.8 + option_similarity * 0.2
            is_exact = row["content_hash"] == target_key
            if is_exact or combined >= minimum_similarity:
                matches.append(
                    SimilarQuestion(
                        question_id=row["id"],
                        version=row["current_version"],
                        display_number=row["display_number"],
                        stem=row["stem"],
                        similarity=100.0 if is_exact else round(combined, 2),
                        is_exact=is_exact,
                    )
                )
        matches.sort(key=lambda item: (not item.is_exact, -item.similarity, item.display_number))
        return matches[:limit]


def _options_text(options) -> str:
    return "|".join(_duplicate_normalize(option.text) for option in options)


def _options_text_from_json(value: str) -> str:
    return "|".join(_duplicate_normalize(item.get("text", "")) for item in json.loads(value))
