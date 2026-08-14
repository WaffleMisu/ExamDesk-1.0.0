from decimal import Decimal

import pytest

from examdesk.domain.enums import MatchMode, QuestionType
from examdesk.domain.models import BlankDefinition, UnorderedGroup
from examdesk.scoring import SimilarityLevel, grade_choice, grade_fill, normalize_answer


@pytest.mark.parametrize("selected", [{"A"}, {"A", "B"}, {"A", "C"}, {"B"}, {"C"}])
def test_multiple_choice_correct_subset_gets_half(selected: set[str]) -> None:
    result = grade_choice(
        QuestionType.MULTIPLE,
        selected,
        {"A", "B", "C"},
        Decimal("2"),
    )
    assert result.score == Decimal("1")
    assert result.is_partial


@pytest.mark.parametrize("selected", [{"D"}, {"A", "D"}, {"A", "B", "C", "D"}, set()])
def test_multiple_choice_with_wrong_option_or_empty_gets_zero(selected: set[str]) -> None:
    result = grade_choice(
        QuestionType.MULTIPLE,
        selected,
        {"A", "B", "C"},
        Decimal("2"),
    )
    assert result.score == Decimal("0")


def test_multiple_choice_full_and_decimal_partial_scores() -> None:
    full = grade_choice(
        QuestionType.MULTIPLE,
        {"A", "B", "C"},
        {"A", "B", "C"},
        Decimal("1.5"),
    )
    partial = grade_choice(
        QuestionType.MULTIPLE,
        {"A", "B"},
        {"A", "B", "C"},
        Decimal("1.5"),
    )
    assert full.score == Decimal("1.5")
    assert partial.score == Decimal("0.75")


def test_answer_normalization_handles_full_width_case_and_spaces() -> None:
    assert normalize_answer("  ＡＢＣ  ") == "abc"
    assert normalize_answer("国土   变更") == "国土 变更"


def test_grouped_unordered_fill_matches_once_and_keeps_fixed_blanks() -> None:
    blanks = [
        BlankDefinition(index, (str(index),), Decimal("1"), MatchMode.STRICT)
        for index in range(1, 9)
    ]
    result = grade_fill(
        ["3", "1", "2", "4", "7", "5", "6", "8"],
        blanks,
        [UnorderedGroup((1, 2, 3)), UnorderedGroup((5, 6, 7))],
    )
    assert result.strict_score == Decimal("8")
    assert all(item.strict_correct for item in result.blanks)


def test_unordered_fill_does_not_reuse_one_expected_answer() -> None:
    blanks = [
        BlankDefinition(index, (str(index),), Decimal("1"), MatchMode.STRICT)
        for index in range(1, 4)
    ]
    result = grade_fill(["1", "1", "1"], blanks, [UnorderedGroup((1, 2, 3))])

    assert result.strict_score == Decimal("1")
    assert sum(item.strict_correct for item in result.blanks) == 1


def test_similar_text_creates_estimated_score_without_changing_strict_score() -> None:
    blanks = [
        BlankDefinition(1, ("永久基本农田区",), Decimal("2"), MatchMode.TEXT_SIMILARITY),
    ]
    result = grade_fill(["永久基本农用区"], blanks, similarity_level=SimilarityLevel.STANDARD)

    assert result.strict_score == Decimal("0")
    assert result.estimated_score == Decimal("2")
    assert result.blanks[0].similar_match is not None


def test_custom_similarity_threshold_can_flag_a_core_substring_for_review() -> None:
    blanks = [BlankDefinition(1, ("我有一个梦想",), Decimal("2"), MatchMode.TEXT_SIMILARITY)]

    accepted = grade_fill(
        ["有梦想"],
        blanks,
        similarity_level=SimilarityLevel.CUSTOM,
        custom_similarity_threshold=66.0,
    )
    rejected = grade_fill(
        ["有梦想"],
        blanks,
        similarity_level=SimilarityLevel.CUSTOM,
        custom_similarity_threshold=67.0,
    )

    assert accepted.strict_score == Decimal("0")
    assert accepted.estimated_score == Decimal("2")
    assert accepted.blanks[0].similar_match is not None
    assert rejected.estimated_score == Decimal("0")


@pytest.mark.parametrize("threshold", [None, 49.9, 100.1])
def test_custom_similarity_threshold_must_be_in_range(threshold: float | None) -> None:
    blanks = [BlankDefinition(1, ("我有一个梦想",), Decimal("2"))]
    with pytest.raises(ValueError, match="threshold"):
        grade_fill(
            ["有梦想"],
            blanks,
            similarity_level=SimilarityLevel.CUSTOM,
            custom_similarity_threshold=threshold,
        )


@pytest.mark.parametrize("response", ["12345", "2026-08-04", "DLBM"])
def test_machine_values_do_not_receive_similarity_estimate(response: str) -> None:
    blanks = [BlankDefinition(1, (response + "X",), Decimal("1"))]
    result = grade_fill([response], blanks)
    assert result.strict_score == Decimal("0")
    assert result.estimated_score == Decimal("0")


def test_short_text_does_not_receive_similarity_estimate() -> None:
    blanks = [BlankDefinition(1, ("耕地",), Decimal("1"))]
    result = grade_fill(["林地"], blanks, similarity_level=SimilarityLevel.LOOSE)
    assert result.estimated_score == Decimal("0")


def test_unordered_group_rejects_duplicate_equivalent_answers() -> None:
    blanks = [
        BlankDefinition(1, ("耕地",), Decimal("1")),
        BlankDefinition(2, ("耕地",), Decimal("1")),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        grade_fill(["耕地", "耕地"], blanks, [UnorderedGroup((1, 2))])
