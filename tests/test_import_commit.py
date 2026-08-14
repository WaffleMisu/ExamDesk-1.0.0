import io
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType, UsageScope
from examdesk.domain.models import BlankDefinition, QuestionDraft, QuestionOption
from examdesk.importers import ImportCandidate, ImportCommitService, PendingImage
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.security.passwords import hash_secret


def image_bytes(color: tuple[int, int, int] = (20, 100, 180)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (100, 60), color).save(output, format="PNG")
    return output.getvalue()


def test_import_commit_materializes_images_and_skips_exact_duplicate(tmp_path: Path) -> None:
    database_path = tmp_path / "import.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    service = ImportCommitService(repository, AssetManager(database, tmp_path / "assets"))
    question = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="带图题目",
        basis="依据",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )
    candidate = ImportCandidate("测试第1题", question, [PendingImage("stem", image_bytes(), "题图.png")])

    first = service.commit([candidate], actor_id=None)
    second = service.commit([candidate], actor_id=None)

    assert len(first.saved) == 1
    assert second.skipped_exact_duplicates == ["测试第1题"]
    loaded = repository.get(first.saved[0].id)
    assert len(loaded.question_asset_ids) == 1


def test_import_commit_deduplicates_identical_images_for_the_same_owner(tmp_path: Path) -> None:
    database_path = tmp_path / "duplicate-images.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    service = ImportCommitService(repository, AssetManager(database, tmp_path / "assets"))
    question = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="同一位置重复图片",
        basis="",
        display_number="006",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )
    data = image_bytes()
    candidate = ImportCandidate(
        "Sheet1!6",
        question,
        [
            PendingImage("stem", data, "题图1.png"),
            PendingImage("stem", data, "题图2.png"),
            PendingImage("A", data, "A图.png"),
        ],
    )

    result = service.commit([candidate], actor_id=None)

    assert result.errors == []
    assert result.deduplicated_images == 1
    loaded = repository.get(result.saved[0].id)
    assert len(loaded.question_asset_ids) == 1
    assert len(loaded.options[0].asset_ids) == 1


def test_image_judgement_questions_with_different_images_are_not_duplicates(tmp_path: Path) -> None:
    database_path = tmp_path / "image-judgement.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    service = ImportCommitService(repository, AssetManager(database, tmp_path / "assets"))

    def candidate(location: str, answer: str, color: tuple[int, int, int]) -> ImportCandidate:
        question = QuestionDraft(
            question_type=QuestionType.FILL,
            stem="根据材料判断类别，当前操作类别为_____（填写规范编号）",
            basis="",
            status=QuestionStatus.ENABLED,
            blanks=[BlankDefinition(1, (answer,), Decimal("1"))],
            score=Decimal("1"),
        )
        return ImportCandidate(location, question, [PendingImage("stem", image_bytes(color), "题图.png")])

    result = service.commit(
        [
            candidate("题库!33", "0102", (20, 100, 180)),
            candidate("题库!34", "0201", (180, 100, 20)),
        ],
        actor_id=None,
    )

    assert len(result.saved) == 2
    assert result.skipped_exact_duplicates == []
    assert result.answer_conflicts == []


def test_same_image_and_stem_with_different_answer_is_imported_as_draft_conflict(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "answer-conflict.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    service = ImportCommitService(repository, AssetManager(database, tmp_path / "assets"))
    source_image = image_bytes()

    def candidate(location: str, answer: str) -> ImportCandidate:
        question = QuestionDraft(
            question_type=QuestionType.FILL,
            stem="根据材料判断类别，当前操作类别为_____（填写规范编号）",
            basis="",
            status=QuestionStatus.ENABLED,
            blanks=[BlankDefinition(1, (answer,), Decimal("1"))],
            score=Decimal("1"),
        )
        return ImportCandidate(location, question, [PendingImage("stem", source_image, "题图.png")])

    first = service.commit([candidate("题库!33", "0102")], actor_id=None)
    conflict = service.commit([candidate("题库!34", "0201")], actor_id=None)
    duplicate = service.commit([candidate("题库!35", "0102")], actor_id=None)

    assert len(first.saved) == 1
    assert conflict.answer_conflicts == ["题库!34"]
    conflicted_question = repository.get(conflict.saved[0].id)
    assert conflicted_question.status is QuestionStatus.DRAFT
    assert "答案冲突待复核" in conflicted_question.tags
    assert duplicate.skipped_exact_duplicates == ["题库!35"]


def test_same_surface_and_answer_with_different_score_is_imported_as_draft_conflict(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "score-conflict.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    service = ImportCommitService(repository, AssetManager(database, tmp_path / "assets"))
    first = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="同题不同分值",
        basis="",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "正确"), QuestionOption("B", "错误")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )
    second = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem=first.stem,
        basis="",
        status=QuestionStatus.ENABLED,
        options=list(first.options),
        correct_option_keys={"A"},
        score=Decimal("2"),
    )

    service.commit([ImportCandidate("题库!1", first)], actor_id=None)
    conflict = service.commit([ImportCandidate("题库!2", second)], actor_id=None)

    assert conflict.answer_conflicts == ["题库!2"]
    assert repository.get(conflict.saved[0].id).status is QuestionStatus.DRAFT


def test_supervisor_can_update_by_number_and_preserve_images(tmp_path: Path) -> None:
    database_path = tmp_path / "update.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    admins = AdminRepository(database)
    supervisor = admins.create_first_admin("主管理员", "supervisor-pass", hash_secret("RECOVERY").encode())
    ordinary = admins.add_admin(supervisor.id, "管理员", "admin-pass")
    assets = AssetManager(database, tmp_path / "assets")
    image = assets.ingest_bytes(image_bytes(), "原图.png")
    repository = QuestionRepository(database)
    original = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="原题目",
        basis="原依据",
        display_number="100",
        status=QuestionStatus.ENABLED,
        usage_scope=UsageScope.BOTH,
        options=[QuestionOption("A", "甲", (image.id,)), QuestionOption("B", "乙")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )
    repository.create(original, supervisor.id)
    incoming = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="更新后的题目",
        basis="",
        display_number="100",
        status=QuestionStatus.DISABLED,
        usage_scope=UsageScope.EXAM_ONLY,
        applicable_year=2026,
        source="新来源",
        chapter="新章节",
        clause="新条款",
        difficulty="较难",
        tags=["重点"],
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"B"},
        score=Decimal("2"),
    )
    candidate = ImportCandidate(
        "题库!2",
        incoming,
        provided_fields=frozenset(
            {"依据", "状态", "使用范围", "适用年份", "来源", "章节", "条款", "难度", "标签"}
        ),
    )
    service = ImportCommitService(repository, assets)

    with pytest.raises(PermissionError, match="主管理员"):
        service.commit([candidate], actor_id=ordinary.id, update_by_number=True)

    result = service.commit([candidate], actor_id=supervisor.id, update_by_number=True)
    loaded = repository.get(original.id)

    assert result.errors == []
    assert result.saved[0].version == 2
    assert loaded.stem == "更新后的题目"
    assert loaded.status is QuestionStatus.DISABLED
    assert loaded.usage_scope is UsageScope.EXAM_ONLY
    assert loaded.applicable_year == 2026
    assert loaded.basis == ""
    assert loaded.options[0].asset_ids == (image.id,)
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT action, details_json FROM audit_events WHERE action = 'excel_update_question'"
        ).fetchone()
    assert audit["action"] == "excel_update_question"
    assert "stem" in audit["details_json"]


def test_new_import_rejects_existing_nonempty_number(tmp_path: Path) -> None:
    database_path = tmp_path / "number.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    service = ImportCommitService(repository, AssetManager(database, tmp_path / "assets"))
    first = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="第一题",
        basis="",
        display_number="001",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )
    repository.create(first, actor_id=None)
    second = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="另一道题",
        basis="",
        display_number="001",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"B"},
        score=Decimal("1"),
    )

    result = service.commit([ImportCandidate("题库!2", second)], actor_id=None)

    assert result.saved == []
    assert "编号已存在" in result.errors[0][1]
