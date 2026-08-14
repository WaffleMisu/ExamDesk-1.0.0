from decimal import Decimal
from pathlib import Path

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import MatchMode, QuestionStatus, QuestionType, UsageScope
from examdesk.domain.models import BlankDefinition, QuestionDraft, QuestionOption
from examdesk.packages import SigningKeyPair
from examdesk.practice import (
    PracticeDefinition,
    PracticeFilter,
    PracticePackageReader,
    PracticeService,
    PracticeSession,
)
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.scoring import SimilarityLevel


def make_question(identifier: str, question_type: QuestionType) -> QuestionDraft:
    correct = {"A", "B"} if question_type is QuestionType.MULTIPLE else {"A"}
    return QuestionDraft(
        id=identifier,
        question_type=question_type,
        stem=identifier,
        basis="依据",
        status=QuestionStatus.ENABLED,
        usage_scope=UsageScope.BOTH,
        applicable_year=2026,
        chapter="第一章",
        tags=["安全规范"],
        options=[
            QuestionOption("A", "甲"),
            QuestionOption("B", "乙"),
            QuestionOption("C", "丙"),
            QuestionOption("D", "丁"),
        ],
        correct_option_keys=correct,
        score=Decimal("2"),
    )


def test_practice_package_session_grading_and_wrong_book(tmp_path: Path) -> None:
    database_path = tmp_path / "practice.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    repository.create(make_question("single-1", QuestionType.SINGLE), actor_id="admin")
    repository.create(make_question("single-2", QuestionType.SINGLE), actor_id="admin")
    repository.create(make_question("multiple-1", QuestionType.MULTIPLE), actor_id="admin")
    assets = AssetManager(database, tmp_path / "assets")
    service = PracticeService(database, repository, assets)
    signer = SigningKeyPair.generate()

    package = service.export_package(
        name="2026年练习题库",
        practice_filter=PracticeFilter(
            applicable_year=2026,
            chapters=frozenset({"第一章"}),
        ),
        distribution_password="practice-key",
        signer=signer,
        minimum_software_version="2.0.0",
    )
    definition = PracticePackageReader.open(
        package,
        distribution_password="practice-key",
        trusted_signers={signer.id: signer.public_key},
    )
    session = service.start_session(
        definition,
        {QuestionType.SINGLE: 1, QuestionType.MULTIPLE: 1},
    )
    for question in session.questions:
        session.set_response(question.id, ["D"])
    grade = session.grade()
    service.save_progress(definition, grade)

    assert definition.name == "2026年练习题库"
    assert len(session.questions) == 2
    assert grade.strict_score == Decimal("0")
    assert len(grade.wrong_question_ids) == 2
    assert service.wrong_question_ids(definition.bank_id) == set(grade.wrong_question_ids)


def test_practice_package_uses_its_custom_similarity_threshold(tmp_path: Path) -> None:
    database_path = tmp_path / "practice.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    question = QuestionDraft(
        question_type=QuestionType.FILL,
        stem="请填写句子",
        basis="",
        status=QuestionStatus.ENABLED,
        usage_scope=UsageScope.BOTH,
        blanks=[
            BlankDefinition(1, ("我有一个梦想",), Decimal("2"), MatchMode.TEXT_SIMILARITY)
        ],
        score=Decimal("2"),
    )
    repository.create(question, actor_id="admin")
    service = PracticeService(
        database,
        repository,
        AssetManager(database, tmp_path / "assets"),
    )
    signer = SigningKeyPair.generate()
    package = service.export_package(
        name="自定义相似度练习",
        practice_filter=PracticeFilter(),
        distribution_password="practice-key",
        signer=signer,
        minimum_software_version="2.1.0",
        similarity_level=SimilarityLevel.CUSTOM,
        custom_similarity_threshold=66.0,
    )
    definition = PracticePackageReader.open(
        package,
        distribution_password="practice-key",
        trusted_signers={signer.id: signer.public_key},
    )
    session = service.start_session(definition, {QuestionType.FILL: 1})
    session.set_response(question.id, ["有梦想"])

    grade = session.grade()

    assert definition.similarity_level is SimilarityLevel.CUSTOM
    assert definition.custom_similarity_threshold == 66.0
    assert grade.strict_score == Decimal("0")
    assert grade.estimated_score == Decimal("2")


def test_practice_package_can_be_exported_without_password(tmp_path: Path) -> None:
    database_path = tmp_path / "practice.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    repository.create(make_question("single-1", QuestionType.SINGLE), actor_id="admin")
    service = PracticeService(database, repository, AssetManager(database, tmp_path / "assets"))
    signer = SigningKeyPair.generate()

    package = service.export_package(
        name="无密码练习",
        practice_filter=PracticeFilter(),
        distribution_password="",
        signer=signer,
        minimum_software_version="2.2.2",
    )
    definition = PracticePackageReader.open(
        package,
        distribution_password="",
        trusted_signers={signer.id: signer.public_key},
    )

    assert definition.name == "无密码练习"


def test_single_question_grading_matches_final_practice_grade() -> None:
    single = make_question("single", QuestionType.SINGLE)
    multiple = make_question("multiple", QuestionType.MULTIPLE)
    definition = PracticeDefinition("bank", "package", "练习", 1, (single, multiple), {})
    session = PracticeSession(definition, [single, multiple])
    session.set_response(single.id, ["A"])
    session.set_response(multiple.id, ["A"])

    single_grade = session.grade_question(single.id)
    multiple_grade = session.grade_question(multiple.id)
    final_grade = session.grade()

    assert single_grade.strict_score == Decimal("2")
    assert multiple_grade.strict_score == Decimal("1")
    assert final_grade.questions == (single_grade, multiple_grade)
    assert final_grade.strict_score == Decimal("3")
