from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from rapidfuzz.distance import Levenshtein
from rapidfuzz.fuzz import ratio

from examdesk.domain.enums import MatchMode, QuestionType
from examdesk.domain.models import BlankDefinition, UnorderedGroup

WHITESPACE_RE = re.compile(r"\s+")
MACHINE_VALUE_RE = re.compile(r"[A-Za-z0-9./:%+\-年月日]+")


class SimilarityLevel(StrEnum):
    STRICT = "strict"
    STANDARD = "standard"
    LOOSE = "loose"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ChoiceGrade:
    score: Decimal
    max_score: Decimal
    is_correct: bool
    is_partial: bool


@dataclass(frozen=True, slots=True)
class SimilarMatch:
    accepted_answer: str
    similarity: float


@dataclass(frozen=True, slots=True)
class BlankGrade:
    response_index: int
    expected_index: int
    response: str
    strict_correct: bool
    estimated_correct: bool
    strict_score: Decimal
    estimated_score: Decimal
    similar_match: SimilarMatch | None = None


@dataclass(frozen=True, slots=True)
class FillGrade:
    strict_score: Decimal
    estimated_score: Decimal
    max_score: Decimal
    blanks: tuple[BlankGrade, ...]


def normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip().casefold()
    return WHITESPACE_RE.sub(" ", normalized)


def grade_choice(
    question_type: QuestionType,
    selected_keys: set[str],
    correct_keys: set[str],
    max_score: Decimal,
) -> ChoiceGrade:
    selected = {key.strip().upper() for key in selected_keys if key.strip()}
    correct = {key.strip().upper() for key in correct_keys if key.strip()}

    if max_score <= 0:
        raise ValueError("max score must be positive")
    if not correct:
        raise ValueError("correct answer must not be empty")

    if question_type in (QuestionType.SINGLE, QuestionType.JUDGE):
        is_correct = selected == correct
        return ChoiceGrade(
            score=max_score if is_correct else Decimal("0"),
            max_score=max_score,
            is_correct=is_correct,
            is_partial=False,
        )

    if question_type is not QuestionType.MULTIPLE:
        raise ValueError("choice grading requires a choice or judge question")

    if selected == correct:
        return ChoiceGrade(max_score, max_score, True, False)
    if selected and selected < correct:
        return ChoiceGrade(max_score * Decimal("0.5"), max_score, False, True)
    return ChoiceGrade(Decimal("0"), max_score, False, False)


def grade_fill(
    responses: list[str],
    blanks: list[BlankDefinition],
    unordered_groups: list[UnorderedGroup] | None = None,
    similarity_level: SimilarityLevel = SimilarityLevel.STANDARD,
    custom_similarity_threshold: float | None = None,
) -> FillGrade:
    if not blanks:
        raise ValueError("fill question must have at least one blank")
    _validate_blank_indexes(blanks)
    groups = unordered_groups or []
    _validate_groups(blanks, groups)
    validate_similarity_settings(similarity_level, custom_similarity_threshold)

    padded_responses = list(responses[: len(blanks)])
    padded_responses.extend([""] * (len(blanks) - len(padded_responses)))

    details: dict[int, BlankGrade] = {}
    grouped_indexes: set[int] = set()
    by_index = {blank.index: blank for blank in blanks}

    for group in groups:
        group_indexes = list(group.indexes)
        grouped_indexes.update(group_indexes)
        group_responses = {index: padded_responses[index - 1] for index in group_indexes}
        strict_edges = _build_edges(group_responses, by_index, group_indexes, None)
        estimated_edges = _build_edges(
            group_responses,
            by_index,
            group_indexes,
            similarity_level,
            custom_similarity_threshold,
        )
        strict_matches = _maximum_matching(group_indexes, strict_edges)
        estimated_matches = _maximum_matching(group_indexes, estimated_edges)

        for response_index in group_indexes:
            expected_index = estimated_matches.get(
                response_index,
                strict_matches.get(response_index, response_index),
            )
            blank = by_index[expected_index]
            strict_correct = response_index in strict_matches
            estimated_correct = response_index in estimated_matches
            similar = None
            if estimated_correct and not strict_correct:
                similar = _find_similar_match(
                    group_responses[response_index],
                    blank,
                    similarity_level,
                    custom_similarity_threshold,
                )
            details[response_index] = BlankGrade(
                response_index=response_index,
                expected_index=expected_index,
                response=group_responses[response_index],
                strict_correct=strict_correct,
                estimated_correct=estimated_correct,
                strict_score=blank.score if strict_correct else Decimal("0"),
                estimated_score=blank.score if estimated_correct else Decimal("0"),
                similar_match=similar,
            )

    for blank in blanks:
        if blank.index in grouped_indexes:
            continue
        response = padded_responses[blank.index - 1]
        strict_correct = _is_exact_match(response, blank)
        similar = (
            None
            if strict_correct
            else _find_similar_match(
                response,
                blank,
                similarity_level,
                custom_similarity_threshold,
            )
        )
        estimated_correct = strict_correct or similar is not None
        details[blank.index] = BlankGrade(
            response_index=blank.index,
            expected_index=blank.index,
            response=response,
            strict_correct=strict_correct,
            estimated_correct=estimated_correct,
            strict_score=blank.score if strict_correct else Decimal("0"),
            estimated_score=blank.score if estimated_correct else Decimal("0"),
            similar_match=similar,
        )

    ordered_details = tuple(details[index] for index in sorted(details))
    return FillGrade(
        strict_score=sum((item.strict_score for item in ordered_details), Decimal("0")),
        estimated_score=sum((item.estimated_score for item in ordered_details), Decimal("0")),
        max_score=sum((blank.score for blank in blanks), Decimal("0")),
        blanks=ordered_details,
    )


def _validate_blank_indexes(blanks: list[BlankDefinition]) -> None:
    indexes = [blank.index for blank in blanks]
    if indexes != list(range(1, len(blanks) + 1)):
        raise ValueError("blank indexes must be continuous and start at 1")
    for blank in blanks:
        if blank.score <= 0:
            raise ValueError("blank score must be positive")
        if not blank.accepted_answers or any(not normalize_answer(value) for value in blank.accepted_answers):
            raise ValueError("accepted answers must not be empty")


def _validate_groups(blanks: list[BlankDefinition], groups: list[UnorderedGroup]) -> None:
    valid_indexes = {blank.index for blank in blanks}
    seen: set[int] = set()
    by_index = {blank.index: blank for blank in blanks}
    for group in groups:
        indexes = set(group.indexes)
        if len(indexes) < 2:
            raise ValueError("unordered group must contain at least two blanks")
        if not indexes <= valid_indexes:
            raise ValueError("unordered group index is out of range")
        if seen & indexes:
            raise ValueError("unordered groups must not overlap")
        seen.update(indexes)
        scores = {by_index[index].score for index in indexes}
        if len(scores) != 1:
            raise ValueError("unordered group blanks must have equal scores")

        normalized_answers: set[str] = set()
        for index in indexes:
            for answer in by_index[index].accepted_answers:
                normalized = normalize_answer(answer)
                if normalized in normalized_answers:
                    raise ValueError("unordered group contains duplicate equivalent answers")
                normalized_answers.add(normalized)


def _is_exact_match(response: str, blank: BlankDefinition) -> bool:
    normalized = normalize_answer(response)
    return bool(normalized) and normalized in {
        normalize_answer(answer) for answer in blank.accepted_answers
    }


def _build_edges(
    responses: dict[int, str],
    blanks_by_index: dict[int, BlankDefinition],
    expected_indexes: list[int],
    similarity_level: SimilarityLevel | None,
    custom_similarity_threshold: float | None = None,
) -> dict[int, list[int]]:
    edges: dict[int, list[int]] = {}
    for response_index, response in responses.items():
        matches: list[int] = []
        for expected_index in expected_indexes:
            blank = blanks_by_index[expected_index]
            if _is_exact_match(response, blank) or similarity_level is not None and _find_similar_match(
                response,
                blank,
                similarity_level,
                custom_similarity_threshold,
            ) is not None:
                matches.append(expected_index)
        edges[response_index] = matches
    return edges


def _maximum_matching(
    response_indexes: list[int],
    edges: dict[int, list[int]],
) -> dict[int, int]:
    expected_to_response: dict[int, int] = {}

    def augment(response_index: int, visited: set[int]) -> bool:
        for expected_index in edges.get(response_index, []):
            if expected_index in visited:
                continue
            visited.add(expected_index)
            previous_response = expected_to_response.get(expected_index)
            if previous_response is None or augment(previous_response, visited):
                expected_to_response[expected_index] = response_index
                return True
        return False

    for response_index in response_indexes:
        augment(response_index, set())
    return {response_index: expected_index for expected_index, response_index in expected_to_response.items()}


def _find_similar_match(
    response: str,
    blank: BlankDefinition,
    level: SimilarityLevel,
    custom_similarity_threshold: float | None = None,
) -> SimilarMatch | None:
    if blank.match_mode is MatchMode.STRICT:
        return None
    normalized_response = normalize_answer(response)
    if not normalized_response or _looks_machine_value(normalized_response):
        return None

    candidates: list[tuple[float, str]] = []
    for accepted in blank.accepted_answers:
        normalized_accepted = normalize_answer(accepted)
        if _looks_machine_value(normalized_accepted):
            continue
        length = max(len(normalized_response), len(normalized_accepted))
        if length <= 2:
            continue
        similarity = float(ratio(normalized_response, normalized_accepted))
        if _passes_threshold(
            normalized_response,
            normalized_accepted,
            similarity,
            level,
            custom_similarity_threshold,
        ):
            candidates.append((similarity, accepted))

    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda item: item[0])
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 5.0:
        return None
    return SimilarMatch(accepted_answer=candidates[0][1], similarity=candidates[0][0])


def _passes_threshold(
    response: str,
    accepted: str,
    similarity: float,
    level: SimilarityLevel,
    custom_similarity_threshold: float | None,
) -> bool:
    length = max(len(response), len(accepted))
    if length <= 2:
        return False
    if level is SimilarityLevel.CUSTOM:
        return similarity >= float(custom_similarity_threshold)
    if 3 <= length <= 5:
        if level is SimilarityLevel.STRICT:
            return False
        return Levenshtein.distance(response, accepted) <= 1
    threshold = {
        SimilarityLevel.STRICT: 90.0,
        SimilarityLevel.STANDARD: 85.0,
        SimilarityLevel.LOOSE: 80.0,
    }[level]
    return similarity >= threshold


def validate_similarity_settings(
    level: SimilarityLevel,
    custom_similarity_threshold: float | None,
) -> None:
    if level is SimilarityLevel.CUSTOM:
        if custom_similarity_threshold is None or not 50.0 <= custom_similarity_threshold <= 100.0:
            raise ValueError("custom similarity threshold must be between 50 and 100")
        return
    if custom_similarity_threshold is not None:
        raise ValueError("custom similarity threshold requires custom level")


def _looks_machine_value(value: str) -> bool:
    return bool(MACHINE_VALUE_RE.fullmatch(value))
