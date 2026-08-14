from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePath

from examdesk.domain.enums import QuestionStatus, QuestionType, UsageScope
from examdesk.domain.models import (
    QuestionDraft,
    QuestionOption,
)
from examdesk.questions.validation import validate_question

from .fill_syntax import parse_fill_answer
from .models import ImportCandidate, ImportIssue, ImportPreview, PendingImage

REQUIRED_HEADERS = {"编号", "题型", "题目", "答案", "分值"}
QUESTION_TYPE_MAP = {
    "单选": QuestionType.SINGLE,
    "单选题": QuestionType.SINGLE,
    "多选": QuestionType.MULTIPLE,
    "多选题": QuestionType.MULTIPLE,
    "判断": QuestionType.JUDGE,
    "判断题": QuestionType.JUDGE,
    "填空": QuestionType.FILL,
    "填空题": QuestionType.FILL,
}
STATUS_MAP = {
    "草稿": QuestionStatus.DRAFT,
    "启用": QuestionStatus.ENABLED,
    "停用": QuestionStatus.DISABLED,
}
SCOPE_MAP = {
    "考试和练习": UsageScope.BOTH,
    "练习和考试": UsageScope.BOTH,
    "仅考试": UsageScope.EXAM_ONLY,
    "仅练习": UsageScope.PRACTICE_ONLY,
}
OPTION_KEYS = ("A", "B", "C", "D")
OPTION_ANSWER_RE = re.compile(r"[A-D]", re.IGNORECASE)


@dataclass(slots=True)
class LegacyQuestionRecord:
    source_row: int
    question: QuestionDraft
    image_paths: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(slots=True)
class LegacyImportResult:
    encoding: str
    delimiter: str
    questions: list[LegacyQuestionRecord]
    issues: list[ImportIssue]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)


def parse_legacy_txt(path: Path) -> LegacyImportResult:
    raw = path.read_bytes()
    text, encoding = _decode_legacy_text(raw)
    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        raise ValueError("题库文件为空")

    headers = [header.strip().lstrip("\ufeff") for header in rows[0]]
    missing = REQUIRED_HEADERS - set(headers)
    if missing:
        raise ValueError("题库缺少表头：{}".format("、".join(sorted(missing))))

    questions: list[LegacyQuestionRecord] = []
    issues: list[ImportIssue] = []
    seen_numbers: dict[str, int] = {}
    for row_number, values in enumerate(rows[1:], start=2):
        if not any(value.strip() for value in values):
            continue
        padded = values + [""] * max(0, len(headers) - len(values))
        row = dict(zip(headers, padded, strict=False))
        display_number = row.get("编号", "").strip()
        if display_number in seen_numbers:
            issues.append(
                ImportIssue(
                    row_number,
                    "error",
                    "duplicate_number",
                    "编号",
                    f"编号与第{seen_numbers[display_number]}行重复",
                )
            )
        elif display_number:
            seen_numbers[display_number] = row_number

        try:
            record = parse_legacy_row(row, row_number)
        except ValueError as exc:
            issues.append(ImportIssue(row_number, "error", "parse", "", str(exc)))
            continue
        questions.append(record)
        for validation_issue in validate_question(record.question):
            issues.append(
                ImportIssue(
                    row_number,
                    "error",
                    validation_issue.code,
                    validation_issue.field,
                    validation_issue.message,
                )
            )
        issues.extend(_validate_image_paths(record, path.parent))

    return LegacyImportResult(
        encoding=encoding,
        delimiter=delimiter,
        questions=questions,
        issues=issues,
    )


def parse_legacy_txt_preview(path: Path) -> ImportPreview:
    result = parse_legacy_txt(path)
    candidates = []
    for record in result.questions:
        images = []
        for owner, values in record.image_paths.items():
            for value in values:
                pure_path = PurePath(value)
                if pure_path.is_absolute() or ".." in pure_path.parts:
                    continue
                image_path = path.parent / Path(*pure_path.parts)
                try:
                    data = image_path.read_bytes()
                except OSError:
                    continue
                images.append(PendingImage(owner, data, image_path.name))
        candidates.append(
            ImportCandidate(
                f"第{record.source_row}行",
                record.question,
                images,
                frozenset(),
            )
        )
    return ImportPreview("txt", candidates, result.issues)


def _decode_legacy_text(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别题库编码，请另存为GBK或UTF-8")


def _detect_delimiter(text: str) -> str:
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    if first_line.count("\t") >= first_line.count("|") and "\t" in first_line:
        return "\t"
    if "|" in first_line:
        return "|"
    raise ValueError("题库必须使用制表符或英文竖线分隔")


def parse_legacy_row(row: dict[str, str], row_number: int) -> LegacyQuestionRecord:
    type_text = row.get("题型", "").strip()
    question_type = QUESTION_TYPE_MAP.get(type_text)
    if question_type is None:
        raise ValueError("第{}行题型无效：{}".format(row_number, type_text or "空"))

    status_text = row.get("状态", "启用").strip() or "启用"
    status = STATUS_MAP.get(status_text)
    if status is None:
        raise ValueError(f"第{row_number}行状态无效：{status_text}")
    scope_text = row.get("使用范围", "考试和练习").strip() or "考试和练习"
    usage_scope = SCOPE_MAP.get(scope_text)
    if usage_scope is None:
        raise ValueError(f"第{row_number}行使用范围无效：{scope_text}")
    applicable_year = _parse_year(row.get("适用年份", ""), row_number)
    common = {
        "question_type": question_type,
        "stem": row.get("题目", "").strip(),
        "basis": row.get("依据", "").strip(),
        "display_number": row.get("编号", "").strip(),
        "status": status,
        "usage_scope": usage_scope,
        "applicable_year": applicable_year,
        "source": row.get("来源", "").strip(),
        "chapter": row.get("章节", "").strip(),
        "clause": row.get("条款", "").strip(),
        "difficulty": row.get("难度", "").strip(),
        "tags": _split_tags(row.get("标签", "")),
    }
    image_paths = _extract_image_paths(row)
    if question_type is QuestionType.FILL:
        question = _parse_fill_question(common, row)
    else:
        question = _parse_choice_question(common, row, image_paths)
    return LegacyQuestionRecord(row_number, question, image_paths)


def _parse_choice_question(
    common: dict,
    row: dict[str, str],
    image_paths: dict[str, tuple[str, ...]],
) -> QuestionDraft:
    options = []
    for key in OPTION_KEYS:
        text = row.get(key, "").strip()
        assets = image_paths.get(key, ())
        if text or assets:
            options.append(QuestionOption(key, text, assets))
    answer_text = row.get("答案", "").strip().upper()
    correct_keys = set(OPTION_ANSWER_RE.findall(answer_text))
    score = _parse_decimal(row.get("分值", ""), "题目分值")
    return QuestionDraft(
        **common,
        options=options,
        correct_option_keys=correct_keys,
        question_asset_ids=list(image_paths.get("stem", ())),
        score=score,
    )


def _parse_fill_question(common: dict, row: dict[str, str]) -> QuestionDraft:
    blanks, groups, total_score = parse_fill_answer(
        row.get("答案", ""),
        row.get("分值", ""),
    )
    return QuestionDraft(
        **common,
        blanks=blanks,
        unordered_groups=groups,
        score=total_score,
    )


def _parse_decimal(value: str, label: str) -> Decimal:
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{label}不是有效数字") from exc
    if result <= 0:
        raise ValueError(f"{label}必须大于0")
    return result


def _parse_year(value: str, row_number: int) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        year = int(cleaned)
    except ValueError as exc:
        raise ValueError(f"第{row_number}行适用年份必须是整数") from exc
    if year < 1 or year > 9999:
        raise ValueError(f"第{row_number}行适用年份范围无效")
    return year


def _split_tags(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.replace(",", ";").split(";") if part.strip()))


def _extract_image_paths(row: dict[str, str]) -> dict[str, tuple[str, ...]]:
    mapping = {"stem": "题图", "A": "A图", "B": "B图", "C": "C图", "D": "D图"}
    result: dict[str, tuple[str, ...]] = {}
    for owner, field_name in mapping.items():
        value = row.get(field_name, "").strip()
        if value:
            paths = tuple(part.strip() for part in value.split(";") if part.strip())
            result[owner] = paths
    return result


def _validate_image_paths(record: LegacyQuestionRecord, base_dir: Path) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for owner, paths in record.image_paths.items():
        for value in paths:
            path = PurePath(value)
            if path.is_absolute() or ".." in path.parts:
                issues.append(
                    ImportIssue(
                        record.source_row,
                        "error",
                        "unsafe_path",
                        owner,
                        f"图片必须使用题库目录内的相对路径：{value}",
                    )
                )
                continue
            if not (base_dir / Path(*path.parts)).exists():
                issues.append(
                    ImportIssue(
                        record.source_row,
                        "warning",
                        "missing_image",
                        owner,
                        f"未找到图片：{value}",
                    )
                )
    return issues
