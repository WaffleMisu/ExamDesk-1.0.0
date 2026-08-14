from decimal import Decimal

from examdesk.domain.enums import MatchMode, QuestionStatus, QuestionType
from examdesk.domain.models import (
    BlankDefinition,
    QuestionDraft,
    QuestionOption,
    UnorderedGroup,
)
from examdesk.questions import expand_blank_scores, validate_question


def test_single_fill_score_expands_to_all_blanks() -> None:
    assert expand_blank_scores("1", 8) == [Decimal("1")] * 8
    assert expand_blank_scores("0.5;1;2", 3) == [
        Decimal("0.5"),
        Decimal("1"),
        Decimal("2"),
    ]


def test_fill_score_count_must_be_one_or_blank_count() -> None:
    try:
        expand_blank_scores("1;2", 8)
    except ValueError as exc:
        assert "score count" in str(exc)
    else:
        raise AssertionError("expected invalid score count")


def test_enabled_multiple_choice_allows_empty_basis_but_validates_answer_count() -> None:
    question = QuestionDraft(
        question_type=QuestionType.MULTIPLE,
        stem="应选择哪些内容？",
        basis="",
        status=QuestionStatus.ENABLED,
        options=[
            QuestionOption("A", "甲"),
            QuestionOption("B", "乙"),
            QuestionOption("C", "丙"),
            QuestionOption("D", "丁"),
        ],
        correct_option_keys={"A"},
        score=Decimal("2"),
    )
    codes = {issue.code for issue in validate_question(question)}
    assert "required" not in codes
    assert "count" in codes


def test_fill_validation_detects_overlapping_unordered_groups() -> None:
    question = QuestionDraft(
        question_type=QuestionType.FILL,
        stem="（1）（2）（3）",
        basis="依据",
        blanks=[
            BlankDefinition(1, ("甲",), Decimal("1"), MatchMode.STRICT),
            BlankDefinition(2, ("乙",), Decimal("1"), MatchMode.STRICT),
            BlankDefinition(3, ("丙",), Decimal("1"), MatchMode.STRICT),
        ],
        unordered_groups=[UnorderedGroup((1, 2)), UnorderedGroup((2, 3))],
    )
    issues = validate_question(question)
    assert any(issue.code == "overlap" for issue in issues)
