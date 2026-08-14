from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType, ReviewPolicy, SessionStatus, UsageScope
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.packages import PasswordPackageCodec, SigningKeyPair, X25519KeyPair
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.scoring import SimilarityLevel
from examdesk.sessions import ExamPackageReader, RosterEntry, SessionError, SessionFilter, SessionService


def make_question(number: int, question_type: QuestionType) -> QuestionDraft:
    options = [
        QuestionOption("A", "甲"),
        QuestionOption("B", "乙"),
        QuestionOption("C", "丙"),
        QuestionOption("D", "丁"),
    ]
    correct = {"A", "B"} if question_type is QuestionType.MULTIPLE else {"A"}
    if question_type is QuestionType.JUDGE:
        options = [QuestionOption("A", "正确"), QuestionOption("B", "错误")]
    return QuestionDraft(
        question_type=question_type,
        stem=f"第{number}题",
        basis="培训手册",
        display_number=f"{number:03d}",
        status=QuestionStatus.ENABLED,
        usage_scope=UsageScope.BOTH,
        applicable_year=2026,
        chapter="第一章",
        difficulty="普通",
        tags=["安全规范"],
        options=options,
        correct_option_keys=correct,
        score=Decimal("2"),
    )


def make_session_service(tmp_path: Path):
    database_path = tmp_path / "session.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    for index in range(1, 9):
        question_type = QuestionType.SINGLE if index <= 4 else QuestionType.MULTIPLE
        repository.create(make_question(index, question_type), actor_id="admin")
    for index in range(9, 12):
        repository.create(make_question(index, QuestionType.JUDGE), actor_id="admin")
    assets = AssetManager(database, tmp_path / "assets")
    return SessionService(database, repository, assets), repository


def create_draft(
    service: SessionService,
    password: str = "600000",
    monitoring_enabled: bool = False,
    similarity_level: SimilarityLevel = SimilarityLevel.STANDARD,
    custom_similarity_threshold: float | None = None,
):
    return service.create_draft(
        name="2026年六月测试",
        description="统一测试",
        password=password,
        session_filter=SessionFilter(
            applicable_year=2026,
            chapters=frozenset({"第一章"}),
        ),
        question_counts={
            QuestionType.SINGLE: 2,
            QuestionType.MULTIPLE: 2,
            QuestionType.JUDGE: 1,
        },
        max_attempts=1,
        roster=[RosterEntry("测试用户甲", "第一组"), RosterEntry("测试用户乙", "第二组")],
        roster_required=True,
        duration_minutes=30,
        review_policy=ReviewPolicy.IMMEDIATE,
        review_release_at=None,
        min_software_version="2.0.0",
        created_by="admin",
        monitoring_enabled=monitoring_enabled,
        similarity_level=similarity_level,
        custom_similarity_threshold=custom_similarity_threshold,
    )


def test_session_selects_fixed_counts_locks_and_exports_exam_package(tmp_path: Path) -> None:
    service, _ = make_session_service(tmp_path)
    draft = create_draft(service)
    assert draft.status is SessionStatus.DRAFT
    assert len(draft.questions) == 5
    assert sum(item.question.question_type is QuestionType.SINGLE for item in draft.questions) == 2
    assert draft.max_score == Decimal("10")

    locked = service.lock(draft.id)
    signer = SigningKeyPair.generate()
    recipient = X25519KeyPair.generate()
    package = service.export_package(
        draft.id,
        password="600000",
        signer=signer,
        result_recipient=recipient,
    )
    opened = ExamPackageReader.open(
        package,
        password="600000",
        trusted_signers={signer.id: signer.public_key},
        current_software_version="2.0.0",
    )

    assert locked.status is SessionStatus.LOCKED
    assert opened.name == draft.name
    assert len(opened.questions) == 5
    assert opened.validate_candidate(" 测试用户甲 ") == "测试用户甲"
    assert opened.similarity_level is SimilarityLevel.STANDARD
    assert opened.custom_similarity_threshold is None
    assert opened.monitoring_enabled is False
    with pytest.raises(ValueError, match="名单"):
        opened.validate_candidate("名单外人员")


def test_monitoring_choice_round_trips_through_exam_package(tmp_path: Path) -> None:
    service, _ = make_session_service(tmp_path)
    draft = create_draft(service, monitoring_enabled=True)
    assert draft.monitoring_enabled is True
    service.lock(draft.id)
    signer = SigningKeyPair.generate()
    package = service.export_package(
        draft.id,
        password="600000",
        signer=signer,
        result_recipient=X25519KeyPair.generate(),
    )

    opened = ExamPackageReader.open(
        package,
        password="600000",
        trusted_signers={signer.id: signer.public_key},
        current_software_version="2.0.0",
    )

    assert opened.monitoring_enabled is True


def test_custom_similarity_settings_round_trip_through_exam_package(tmp_path: Path) -> None:
    service, _ = make_session_service(tmp_path)
    draft = create_draft(
        service,
        similarity_level=SimilarityLevel.CUSTOM,
        custom_similarity_threshold=66.0,
    )
    service.lock(draft.id)
    signer = SigningKeyPair.generate()
    package = service.export_package(
        draft.id,
        password="600000",
        signer=signer,
        result_recipient=X25519KeyPair.generate(),
    )
    opened = ExamPackageReader.open(
        package,
        password="600000",
        trusted_signers={signer.id: signer.public_key},
        current_software_version="2.1.0",
    )

    assert opened.similarity_level is SimilarityLevel.CUSTOM
    assert opened.custom_similarity_threshold == 66.0


def test_session_without_password_exports_open_exam_package(tmp_path: Path) -> None:
    service, _ = make_session_service(tmp_path)
    first = create_draft(service, password="")
    second = create_draft(service, password="")
    assert service.password_required(first.id) is False
    assert service.password_required(second.id) is False
    service.lock(first.id)
    signer = SigningKeyPair.generate()

    package = service.export_package(
        first.id,
        password="",
        signer=signer,
        result_recipient=X25519KeyPair.generate(),
    )
    opened = ExamPackageReader.open(
        package,
        password="",
        trusted_signers={signer.id: signer.public_key},
        current_software_version="2.2.2",
    )

    assert PasswordPackageCodec.requires_password(package) is False
    assert opened.name == first.name


def test_session_rejects_duplicate_password_and_insufficient_questions(tmp_path: Path) -> None:
    service, _ = make_session_service(tmp_path)
    create_draft(service, password="1998")
    with pytest.raises(SessionError, match="密码"):
        create_draft(service, password="1998")

    with pytest.raises(SessionError, match="数量不足"):
        service.create_draft(
            name="题量不足",
            description="",
            password="8888",
            session_filter=SessionFilter(applicable_year=2026),
            question_counts={QuestionType.SINGLE: 100},
            max_attempts=1,
            roster=[],
            roster_required=False,
            duration_minutes=None,
            review_policy=ReviewPolicy.SCORE_ONLY,
            review_release_at=None,
            min_software_version="2.0.0",
            created_by="admin",
        )


def test_available_question_counts_use_the_same_filters_as_selection(tmp_path: Path) -> None:
    service, repository = make_session_service(tmp_path)
    other_tag = make_question(20, QuestionType.SINGLE)
    other_tag.applicable_year = None
    other_tag.tags = ["流程管理"]
    repository.create(other_tag, actor_id="admin")
    disabled = make_question(21, QuestionType.SINGLE)
    disabled.status = QuestionStatus.DISABLED
    repository.create(disabled, actor_id="admin")
    practice_only = make_question(22, QuestionType.SINGLE)
    practice_only.usage_scope = UsageScope.PRACTICE_ONLY
    repository.create(practice_only, actor_id="admin")

    all_counts = service.available_question_counts(SessionFilter())
    assert all_counts == {
        QuestionType.SINGLE: 5,
        QuestionType.MULTIPLE: 4,
        QuestionType.JUDGE: 3,
        QuestionType.FILL: 0,
    }
    year_counts = service.available_question_counts(SessionFilter(applicable_year=2025))
    assert year_counts[QuestionType.SINGLE] == 1
    tag_counts = service.available_question_counts(
        SessionFilter(tags=frozenset({"安全规范"}))
    )
    assert tag_counts[QuestionType.SINGLE] == 4
    any_tag_counts = service.available_question_counts(
        SessionFilter(tags=frozenset({"安全规范", "流程管理"}))
    )
    assert any_tag_counts[QuestionType.SINGLE] == 5


def test_session_can_replace_same_type_before_lock_only(tmp_path: Path) -> None:
    service, repository = make_session_service(tmp_path)
    draft = create_draft(service)
    selected_ids = {item.question_id for item in draft.questions}
    old = next(item for item in draft.questions if item.question.question_type is QuestionType.SINGLE)
    replacement = next(
        question
        for question, _ in repository.list_current()
        if question.question_type is QuestionType.SINGLE and question.id not in selected_ids
    )

    replaced = service.replace_question(draft.id, old.question_id, replacement.id)
    assert replacement.id in {item.question_id for item in replaced.questions}
    service.lock(draft.id)
    with pytest.raises(SessionError, match="锁定"):
        service.replace_question(draft.id, replacement.id, old.question_id)


def test_after_release_policy_requires_release_time(tmp_path: Path) -> None:
    service, _ = make_session_service(tmp_path)
    with pytest.raises(SessionError, match="开放时间"):
        service.create_draft(
            name="延时查看",
            description="",
            password="1234",
            session_filter=SessionFilter(applicable_year=2026),
            question_counts={QuestionType.SINGLE: 1},
            max_attempts=1,
            roster=[],
            roster_required=False,
            duration_minutes=None,
            review_policy=ReviewPolicy.AFTER_RELEASE,
            review_release_at=None,
            min_software_version="2.0.0",
            created_by="admin",
        )

    valid = service.create_draft(
        name="延时查看有效",
        description="",
        password="5678",
        session_filter=SessionFilter(applicable_year=2026),
        question_counts={QuestionType.SINGLE: 1},
        max_attempts=1,
        roster=[],
        roster_required=False,
        duration_minutes=None,
        review_policy=ReviewPolicy.AFTER_RELEASE,
        review_release_at=datetime.now(UTC) + timedelta(hours=1),
        min_software_version="2.0.0",
        created_by="admin",
    )
    assert valid.review_release_at is not None
