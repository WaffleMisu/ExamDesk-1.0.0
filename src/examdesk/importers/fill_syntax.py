from __future__ import annotations

from decimal import Decimal

from examdesk.domain.enums import MatchMode
from examdesk.domain.models import BlankDefinition, UnorderedGroup
from examdesk.questions.validation import expand_blank_scores


def parse_fill_answer(
    answer_text: str,
    score_text: str,
) -> tuple[list[BlankDefinition], list[UnorderedGroup], Decimal]:
    value = answer_text.strip()
    group_text = ""
    if value.startswith("@"):
        if "|" not in value:
            raise ValueError("无序填空答案缺少英文竖线")
        group_text, value = value[1:].split("|", 1)
    answer_parts = value.split(";")
    if answer_parts and not answer_parts[-1].strip():
        answer_parts.pop()
    if not answer_parts:
        raise ValueError("填空题答案不能为空")
    score_values = expand_blank_scores(score_text, len(answer_parts))
    blanks = []
    for index, (answer_part, score) in enumerate(zip(answer_parts, score_values, strict=True), start=1):
        accepted = tuple(item.strip() for item in answer_part.split("/") if item.strip())
        blanks.append(
            BlankDefinition(
                index=index,
                accepted_answers=accepted,
                score=score,
                match_mode=MatchMode.TEXT_SIMILARITY,
            )
        )
    groups = parse_unordered_groups(group_text) if group_text else []
    return blanks, groups, sum(score_values, Decimal("0"))


def parse_unordered_groups(value: str) -> list[UnorderedGroup]:
    groups: list[UnorderedGroup] = []
    for token in (part.strip() for part in value.split(",")):
        if not token:
            continue
        if "-" not in token:
            raise ValueError(f"无序填空分组必须使用起止范围：{token}")
        start_text, end_text = token.split("-", 1)
        try:
            start, end = int(start_text), int(end_text)
        except ValueError as exc:
            raise ValueError(f"无序填空分组格式无效：{token}") from exc
        if start >= end:
            raise ValueError(f"无序填空范围必须从小到大：{token}")
        groups.append(UnorderedGroup(tuple(range(start, end + 1))))
    return groups

