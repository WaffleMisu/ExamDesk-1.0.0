from decimal import Decimal
from pathlib import Path

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.questions import QuestionRepository
from examdesk.questions.duplicates import DuplicateChecker


def make_question(stem: str) -> QuestionDraft:
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


def test_duplicate_checker_returns_exact_and_similar_questions(tmp_path: Path) -> None:
    database_path = tmp_path / "duplicates.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    original = make_question("临时用地复垦后应如何认定？")
    repository.create(original, actor_id=None)
    checker = DuplicateChecker(database)

    exact = checker.find_similar(make_question("临时用地复垦后，应如何认定？"))
    similar = checker.find_similar(make_question("临时用地完成复垦后应怎样认定？"), minimum_similarity=65)

    assert exact[0].is_exact
    assert similar[0].question_id == original.id
    assert not similar[0].is_exact
