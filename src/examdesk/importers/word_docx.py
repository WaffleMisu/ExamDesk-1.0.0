from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from examdesk.domain.enums import QuestionStatus, QuestionType, UsageScope
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.questions.validation import validate_question

from .fill_syntax import parse_fill_answer
from .models import ImportCandidate, ImportIssue, ImportPreview, PendingImage

QUESTION_RE = re.compile(r"^\s*(\d+)\s*[.．、]\s*(.+)$")
OPTION_RE = re.compile(r"^\s*([A-Da-d])\s*[.．、]\s*(.*)$")
METADATA_RE = re.compile(
    r"^\s*(题型|答案|依据|分值|章节|条款|难度|使用范围|适用年度|来源|来源文件)\s*[:：]\s*(.*)$"
)
ANSWER_KEY_RE = re.compile(r"(\d+)\s*[.．、:：\-]\s*([A-Da-d]+|正确|错误|对|错)")

TYPE_MAP = {
    "单选": QuestionType.SINGLE,
    "单选题": QuestionType.SINGLE,
    "多选": QuestionType.MULTIPLE,
    "多选题": QuestionType.MULTIPLE,
    "判断": QuestionType.JUDGE,
    "判断题": QuestionType.JUDGE,
    "填空": QuestionType.FILL,
    "填空题": QuestionType.FILL,
}
SCOPE_MAP = {
    "仅练习": UsageScope.PRACTICE_ONLY,
    "仅考试": UsageScope.EXAM_ONLY,
    "练习和考试": UsageScope.BOTH,
    "考试和练习": UsageScope.BOTH,
}


@dataclass(slots=True)
class _QuestionBuilder:
    number: str
    source_line: int
    stem_parts: list[str]
    options: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    images: list[PendingImage] = field(default_factory=list)
    last_owner: str = "stem"

    @property
    def stem(self) -> str:
        return "\n".join(part for part in self.stem_parts if part).strip()


def parse_word_docx(path: Path) -> ImportPreview:
    document = Document(path)
    builders: list[_QuestionBuilder] = []
    issues: list[ImportIssue] = []
    trailing_answers: dict[str, str] = {}
    current: _QuestionBuilder | None = None
    in_answer_key = False

    for line_number, paragraph in enumerate(_iter_paragraphs(document), start=1):
        text = paragraph.text.strip()
        images = _extract_paragraph_images(document, paragraph)

        if text in {"答案", "参考答案", "答案表", "参考答案表"}:
            if current is not None:
                builders.append(current)
                current = None
            in_answer_key = True
            continue

        if in_answer_key:
            for match in ANSWER_KEY_RE.finditer(text):
                number, answer = match.groups()
                existing = trailing_answers.get(number)
                if existing is not None and _normalize_choice_answer(existing) != _normalize_choice_answer(answer):
                    issues.append(
                        ImportIssue(
                            line_number,
                            "error",
                            "duplicate_answer_key",
                            "答案",
                            f"文末答案表中题号{number}出现冲突答案",
                        )
                    )
                trailing_answers[number] = answer
            continue

        question_match = QUESTION_RE.match(text)
        if question_match:
            if current is not None:
                builders.append(current)
            number, stem = question_match.groups()
            current = _QuestionBuilder(number, line_number, [stem.strip()])
            current.images.extend(_pending_images(images, "stem"))
            continue

        if current is None:
            if text or images:
                issues.append(
                    ImportIssue(
                        line_number,
                        "warning",
                        "ignored_content",
                        "",
                        "题目开始前的内容未导入",
                    )
                )
            continue

        option_match = OPTION_RE.match(text)
        if option_match:
            key, option_text = option_match.groups()
            key = key.upper()
            current.options.setdefault(key, []).append(option_text.strip())
            current.last_owner = key
            current.images.extend(_pending_images(images, key))
            continue

        metadata_match = METADATA_RE.match(text)
        if metadata_match:
            key, value = metadata_match.groups()
            normalized_key = "来源" if key == "来源文件" else key
            current.metadata[normalized_key] = value.strip()
            current.last_owner = "stem"
            current.images.extend(_pending_images(images, "stem"))
            continue

        if text:
            if current.last_owner == "stem":
                current.stem_parts.append(text)
            else:
                current.options.setdefault(current.last_owner, []).append(text)
        current.images.extend(_pending_images(images, current.last_owner))

    if current is not None:
        builders.append(current)

    candidates = []
    for builder in builders:
        try:
            candidate, candidate_issues = _build_candidate(builder, trailing_answers.get(builder.number))
        except ValueError as exc:
            issues.append(
                ImportIssue(builder.source_line, "error", "parse", "", str(exc))
            )
            continue
        candidates.append(candidate)
        issues.extend(candidate_issues)

    known_numbers = {builder.number for builder in builders}
    for number in sorted(set(trailing_answers) - known_numbers, key=lambda value: int(value)):
        issues.append(
            ImportIssue(
                0,
                "warning",
                "orphan_answer",
                "答案",
                f"文末答案表中的题号{number}没有对应题目",
            )
        )
    return ImportPreview("word", candidates, issues)


def _build_candidate(
    builder: _QuestionBuilder,
    trailing_answer: str | None,
) -> tuple[ImportCandidate, list[ImportIssue]]:
    issues: list[ImportIssue] = []
    inline_answer = builder.metadata.get("答案", "").strip()
    if (
        inline_answer
        and trailing_answer
        and _normalize_choice_answer(inline_answer) != _normalize_choice_answer(trailing_answer)
    ):
        issues.append(
            ImportIssue(
                builder.source_line,
                "error",
                "answer_conflict",
                "答案",
                "题后答案与文末答案表不一致",
            )
        )
    answer = inline_answer or (trailing_answer or "")
    question_type = _resolve_question_type(builder, answer)
    usage_scope = _resolve_scope(builder.metadata.get("使用范围", ""), builder.source_line, issues)
    applicable_year = _resolve_year(builder.metadata.get("适用年度", ""), builder.source_line, issues)
    score_text = builder.metadata.get("分值", "").strip() or "1"
    if "分值" not in builder.metadata:
        issues.append(
            ImportIssue(builder.source_line, "warning", "default_score", "分值", "未填写分值，暂按1分")
        )

    pending_asset_ids: dict[str, list[str]] = {}
    for index, image in enumerate(builder.images, start=1):
        pending_asset_ids.setdefault(image.owner_key, []).append(f"pending:{index}")
    options = [
        QuestionOption(
            key,
            "\n".join(builder.options[key]).strip(),
            tuple(pending_asset_ids.get(key, [])),
        )
        for key in sorted(builder.options)
    ]
    common = {
        "question_type": question_type,
        "stem": builder.stem,
        "basis": builder.metadata.get("依据", "").strip(),
        "display_number": builder.number,
        "status": QuestionStatus.DRAFT,
        "usage_scope": usage_scope,
        "applicable_year": applicable_year,
        "source": builder.metadata.get("来源", "").strip(),
        "chapter": builder.metadata.get("章节", "").strip(),
        "clause": builder.metadata.get("条款", "").strip(),
        "difficulty": builder.metadata.get("难度", "").strip(),
        "question_asset_ids": pending_asset_ids.get("stem", []),
    }
    if question_type is QuestionType.FILL:
        blanks, groups, total_score = parse_fill_answer(answer, score_text)
        question = QuestionDraft(
            **common,
            blanks=blanks,
            unordered_groups=groups,
            score=total_score,
        )
    else:
        score = _parse_score(score_text)
        question = QuestionDraft(
            **common,
            options=options,
            correct_option_keys=_choice_answer_keys(answer, question_type),
            score=score,
        )

    if not question.source:
        issues.append(
            ImportIssue(builder.source_line, "warning", "missing_source", "来源", "来源文件待补充")
        )
    if question.applicable_year is None:
        issues.append(
            ImportIssue(builder.source_line, "warning", "missing_year", "适用年度", "暂按长期有效")
        )
    for validation_issue in validate_question(question):
        issues.append(
            ImportIssue(
                builder.source_line,
                "error",
                validation_issue.code,
                validation_issue.field,
                validation_issue.message,
            )
        )
    return ImportCandidate(f"Word第{builder.number}题", question, builder.images), issues


def _resolve_question_type(builder: _QuestionBuilder, answer: str) -> QuestionType:
    explicit = builder.metadata.get("题型", "").strip()
    if explicit:
        question_type = TYPE_MAP.get(explicit)
        if question_type is None:
            raise ValueError(f"题型无效：{explicit}")
        return question_type
    if not builder.options:
        return QuestionType.FILL
    option_a = "".join(builder.options.get("A", [])).strip()
    option_b = "".join(builder.options.get("B", [])).strip()
    if option_a in {"正确", "对"} and option_b in {"错误", "错"}:
        return QuestionType.JUDGE
    return QuestionType.MULTIPLE if len(_normalize_choice_answer(answer)) > 1 else QuestionType.SINGLE


def _resolve_scope(value: str, line: int, issues: list[ImportIssue]) -> UsageScope:
    if not value:
        return UsageScope.BOTH
    scope = SCOPE_MAP.get(value)
    if scope is None:
        issues.append(
            ImportIssue(line, "error", "invalid_scope", "使用范围", f"使用范围无效：{value}")
        )
        return UsageScope.BOTH
    return scope


def _resolve_year(value: str, line: int, issues: list[ImportIssue]) -> int | None:
    if not value or value in {"长期", "长期有效"}:
        return None
    try:
        year = int(value)
    except ValueError:
        issues.append(
            ImportIssue(line, "error", "invalid_year", "适用年度", "适用年度不是有效年份")
        )
        return None
    if not 2000 <= year <= 2100:
        issues.append(
            ImportIssue(line, "error", "invalid_year", "适用年度", "适用年度必须在2000至2100之间")
        )
    return year


def _choice_answer_keys(answer: str, question_type: QuestionType) -> set[str]:
    value = answer.strip()
    if question_type is QuestionType.JUDGE:
        if value in {"正确", "对"}:
            return {"A"}
        if value in {"错误", "错"}:
            return {"B"}
    return set(_normalize_choice_answer(value))


def _normalize_choice_answer(value: str) -> str:
    return "".join(sorted(set(re.findall(r"[A-D]", value.upper()))))


def _parse_score(value: str) -> Decimal:
    try:
        score = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("分值不是有效数字") from exc
    if score <= 0:
        raise ValueError("分值必须大于0")
    return score


def _pending_images(images: list[tuple[bytes, str]], owner: str) -> list[PendingImage]:
    return [PendingImage(owner, data, name) for data, name in images]


def _extract_paragraph_images(
    document: DocumentObject,
    paragraph: Paragraph,
) -> list[tuple[bytes, str]]:
    images = []
    for blip in paragraph._element.xpath(".//a:blip"):
        relationship_id = blip.get(qn("r:embed"))
        if not relationship_id:
            continue
        part = document.part.related_parts.get(relationship_id)
        if part is None or not hasattr(part, "blob"):
            continue
        images.append((part.blob, Path(str(part.partname)).name))
    return images


def _iter_paragraphs(document: DocumentObject):
    seen_cells: set[int] = set()
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row in table.rows:
                for cell in row.cells:
                    identity = id(cell._tc)
                    if identity in seen_cells:
                        continue
                    seen_cells.add(identity)
                    yield from cell.paragraphs
