from decimal import Decimal
from pathlib import Path

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import BlankDefinition, QuestionDraft, QuestionOption
from examdesk.questions import (
    QuestionQuery,
    QuestionRepository,
    QuestionVersionConflict,
)


def make_repository(tmp_path: Path) -> QuestionRepository:
    path = tmp_path / "questions.sqlite3"
    initialize_database(path)
    return QuestionRepository(Database(path))


def make_question(stem: str = "临时用地复垦后应如何认定？") -> QuestionDraft:
    return QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem=stem,
        basis="培训手册第三条",
        display_number="001",
        status=QuestionStatus.ENABLED,
        options=[
            QuestionOption("A", "直接变更"),
            QuestionOption("B", "结合现状认定"),
            QuestionOption("C", "保持原分类"),
            QuestionOption("D", "删除记录"),
        ],
        correct_option_keys={"B"},
        score=Decimal("1"),
    )


def test_question_repository_saves_and_finds_normalized_duplicate(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    original = make_question()
    saved = repository.create(original, actor_id=None)
    duplicate = make_question("临时用地复垦后，应如何认定？")

    matches = repository.find_exact_duplicates(duplicate)

    assert saved.version == 1
    assert [(match.id, match.version) for match in matches] == [(original.id, 1)]


def test_fill_answers_participate_in_exact_duplicate_fingerprint(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    original = QuestionDraft(
        question_type=QuestionType.FILL,
        stem="当前操作类别为_____",
        basis="",
        status=QuestionStatus.ENABLED,
        blanks=[BlankDefinition(1, ("0102",), Decimal("1"))],
        score=Decimal("1"),
    )
    repository.create(original, actor_id=None)
    changed_answer = QuestionDraft(
        question_type=QuestionType.FILL,
        stem=original.stem,
        basis="",
        status=QuestionStatus.ENABLED,
        blanks=[BlankDefinition(1, ("0201",), Decimal("1"))],
        score=Decimal("1"),
    )

    assert repository.find_exact_duplicates(changed_answer) == []
    assert len(repository.find_answer_conflicts(changed_answer)) == 1


def test_question_repository_allows_enabled_question_without_basis(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    question = make_question()
    question.basis = ""

    saved = repository.create(question, actor_id=None)

    assert saved.version == 1
    assert repository.get(question.id).basis == ""


def test_question_repository_naturally_sorts_alphanumeric_numbers(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    for number in ("Q10", "Q2", "Q1"):
        question = make_question(f"Natural number {number}")
        question.display_number = number
        repository.create(question, actor_id=None)

    ascending = repository.list_filtered(
        QuestionQuery(sort_by="display_number"), page=1, page_size=50
    )
    descending = repository.list_filtered(
        QuestionQuery(sort_by="display_number", sort_descending=True), page=1, page_size=50
    )

    assert [item.display_number for item in ascending.items] == ["Q1", "Q2", "Q10"]
    assert [item.display_number for item in descending.items] == ["Q10", "Q2", "Q1"]


def test_question_repository_updates_version_and_detects_stale_writer(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    question = make_question()
    repository.create(question, actor_id=None)
    question.basis = "更新后的依据"

    saved = repository.update(question, actor_id=None, expected_version=1)

    assert saved.version == 2
    assert repository.get(question.id).basis == "更新后的依据"
    assert repository.get(question.id, version=1).basis == "培训手册第三条"
    try:
        repository.update(question, actor_id=None, expected_version=1)
    except QuestionVersionConflict:
        pass
    else:
        raise AssertionError("expected stale version conflict")


def test_question_repository_filters_and_pages_management_fields(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    first = make_question("永久基本农用区如何认定？")
    first.display_number = "001"
    first.source = "2026培训手册"
    first.chapter = "安全规范"
    first.clause = "第三条"
    first.difficulty = "较难"
    first.tags = ["耕地", "重点"]
    second = make_question("重点内容如何认定？")
    second.display_number = "002"
    second.source = "其他资料"
    second.chapter = "重点内容"
    second.difficulty = "一般"
    second.tags = ["重点内容"]
    repository.create(first, actor_id=None)
    repository.create(second, actor_id=None)

    page = repository.list_filtered(
        QuestionQuery(keyword="永久", chapter="安全规范", tags=("重点",)),
        page=1,
        page_size=50,
    )

    assert page.total == 1
    assert page.items[0].id == first.id
    assert repository.filtered_ids(QuestionQuery(source="其他资料")) == (second.id,)
    assert repository.distinct_metadata("difficulty") == ("一般", "较难")
