from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from examdesk.domain.enums import QuestionType
from examdesk.domain.models import QuestionDraft
from examdesk.scoring.engine import normalize_answer


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    field: str
    message: str


def expand_blank_scores(value: str, blank_count: int) -> list[Decimal]:
    if blank_count <= 0:
        raise ValueError("blank count must be positive")
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if len(parts) == 1:
        parts *= blank_count
    if len(parts) != blank_count:
        raise ValueError("score count must be 1 or equal to blank count")
    try:
        scores = [Decimal(part) for part in parts]
    except InvalidOperation as exc:
        raise ValueError("blank score is not a number") from exc
    if any(score <= 0 for score in scores):
        raise ValueError("blank score must be positive")
    return scores


def validate_question(question: QuestionDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not question.stem.strip():
        issues.append(ValidationIssue("required", "stem", "题目内容不能为空"))
    if question.applicable_year is not None and not 2000 <= question.applicable_year <= 2100:
        issues.append(ValidationIssue("range", "applicable_year", "适用年度必须在2000至2100之间"))

    if question.question_type in (
        QuestionType.SINGLE,
        QuestionType.MULTIPLE,
        QuestionType.JUDGE,
    ):
        issues.extend(_validate_choice_question(question))
    elif question.question_type is QuestionType.FILL:
        issues.extend(_validate_fill_question(question))
    return issues


def _validate_choice_question(question: QuestionDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    keys = [option.key.strip().upper() for option in question.options]
    if len(question.options) < 2:
        issues.append(ValidationIssue("count", "options", "选择题至少需要两个选项"))
    if len(keys) != len(set(keys)):
        issues.append(ValidationIssue("duplicate", "options", "选项字母不能重复"))
    if any(not option.text.strip() and not option.asset_ids for option in question.options):
        issues.append(ValidationIssue("required", "options", "每个选项必须包含文字或图片"))
    if not question.correct_option_keys:
        issues.append(ValidationIssue("required", "answer", "选择题必须填写答案"))
    if not question.correct_option_keys <= set(keys):
        issues.append(ValidationIssue("unknown", "answer", "答案包含不存在的选项"))
    if (
        question.question_type in (QuestionType.SINGLE, QuestionType.JUDGE)
        and len(question.correct_option_keys) != 1
    ):
        issues.append(ValidationIssue("count", "answer", "单选或判断题只能有一个正确选项"))
    if question.question_type is QuestionType.MULTIPLE and len(question.correct_option_keys) < 2:
        issues.append(ValidationIssue("count", "answer", "多选题至少需要两个正确选项"))
    if question.question_type is QuestionType.JUDGE and keys != ["A", "B"]:
        issues.append(ValidationIssue("format", "options", "判断题选项必须固定为A、B"))
    if question.score <= 0:
        issues.append(ValidationIssue("range", "score", "题目分值必须大于0"))
    return issues


def _validate_fill_question(question: QuestionDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not question.blanks:
        return [ValidationIssue("count", "blanks", "填空题至少需要一个空")]

    indexes = [blank.index for blank in question.blanks]
    if indexes != list(range(1, len(question.blanks) + 1)):
        issues.append(ValidationIssue("sequence", "blanks", "填空序号必须从1开始连续排列"))
    for blank in question.blanks:
        if blank.score <= 0:
            issues.append(ValidationIssue("range", "blanks.score", "每个空的分值必须大于0"))
        normalized = [normalize_answer(answer) for answer in blank.accepted_answers]
        if not normalized or any(not answer for answer in normalized):
            issues.append(ValidationIssue("required", "blanks.answer", "每个空必须填写合法答案"))
        if len(normalized) != len(set(normalized)):
            issues.append(ValidationIssue("duplicate", "blanks.answer", "同一个空的同义答案不能重复"))

    valid_indexes = set(indexes)
    grouped: set[int] = set()
    by_index = {blank.index: blank for blank in question.blanks}
    for group in question.unordered_groups:
        group_indexes = set(group.indexes)
        if len(group_indexes) < 2:
            issues.append(ValidationIssue("count", "unordered_groups", "无序组至少包含两个空"))
        if not group_indexes <= valid_indexes:
            issues.append(ValidationIssue("range", "unordered_groups", "无序组包含不存在的空"))
            continue
        if grouped & group_indexes:
            issues.append(ValidationIssue("overlap", "unordered_groups", "无序组之间不能重叠"))
        grouped.update(group_indexes)
        if len({by_index[index].score for index in group_indexes}) > 1:
            issues.append(ValidationIssue("score", "unordered_groups", "同一无序组内各空分值必须相同"))
        answers = [
            normalize_answer(answer)
            for index in group_indexes
            for answer in by_index[index].accepted_answers
        ]
        if len(answers) != len(set(answers)):
            issues.append(
                ValidationIssue("duplicate", "unordered_groups", "无序组中不能出现重复等价答案")
            )
    return issues
