import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import (
    QuestionStatus,
    QuestionType,
    ReviewPolicy,
    SubmitReason,
    UsageScope,
)
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.exam import ExamStateStore
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.results import AttemptService, ResultImportService, SubmittedReviewStore
from examdesk.security import OrganizationKeyStore
from examdesk.sessions import ExamPackageReader, SessionFilter, SessionService


def test_complete_offline_exam_package_submission_and_collection(tmp_path: Path) -> None:
    master_path = tmp_path / "master" / "data.sqlite3"
    initialize_database(master_path)
    master_database = Database(master_path)
    questions = QuestionRepository(master_database)
    assets = AssetManager(master_database, tmp_path / "master" / "assets")
    question = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="以下说法正确的是？",
        basis="培训手册第一章",
        status=QuestionStatus.ENABLED,
        usage_scope=UsageScope.BOTH,
        options=[QuestionOption("A", "正确说法"), QuestionOption("B", "错误说法")],
        correct_option_keys={"A"},
        score=Decimal("2"),
    )
    questions.create(question, actor_id="supervisor")
    keys = OrganizationKeyStore(master_database)
    organization_keys = keys.ensure_initialized()
    sessions = SessionService(master_database, questions, assets)
    draft = sessions.create_draft(
        name="离线测试场次",
        description="",
        password="600000",
        session_filter=SessionFilter(),
        question_counts={QuestionType.SINGLE: 1},
        max_attempts=1,
        roster=[],
        roster_required=False,
        duration_minutes=None,
        review_policy=ReviewPolicy.IMMEDIATE,
        review_release_at=None,
        min_software_version="2.0.0",
        created_by="supervisor",
    )
    sessions.lock(draft.id)
    package = sessions.export_package(
        draft.id,
        password="600000",
        signer=organization_keys.signing,
        result_recipient=organization_keys.result_recipient,
    )

    candidate_path = tmp_path / "candidate" / "data.sqlite3"
    initialize_database(candidate_path)
    candidate_database = Database(candidate_path)
    candidate_trust = OrganizationKeyStore(candidate_database)
    candidate_trust.import_trust_certificate(keys.export_trust_certificate())
    definition = ExamPackageReader.open(
        package,
        password="600000",
        trusted_signers=candidate_trust.trusted_public_keys(),
        current_software_version="2.0.0",
    )
    state_key = hashlib.sha256(
        definition.session_auth_key + b"\0exam-state\0" + definition.package_id.encode()
    ).digest()
    state_store = ExamStateStore([tmp_path / "candidate" / "state"], state_key)
    review_store = SubmittedReviewStore(tmp_path / "candidate" / "state" / "submitted_reviews")
    attempts = AttemptService(candidate_database, state_store, review_store)
    state = attempts.start(
        definition,
        candidate_name="测试用户甲",
        software_version="2.0.0",
        now=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
    )
    state.set_response(question.id, ["A"])
    artifact = attempts.finalize(
        definition,
        state,
        reason=SubmitReason.MANUAL,
        foreground_events=[],
        monitor_status="ok",
        local_result_directory=tmp_path / "candidate" / "results",
        submission_directory=tmp_path / "candidate" / "待提交答题记录",
        submitted_at=datetime(2026, 8, 4, 9, 10, tzinfo=UTC),
    )

    assert artifact.grade.strict_score == Decimal("2")
    assert review_store.load_latest(definition, "测试用户甲").details_visible
    imported = ResultImportService(
        master_database,
        organization_keys.result_recipient,
    ).import_file(artifact.submission_file, imported_by="supervisor")
    assert imported.imported
    assert imported.candidate_name == "测试用户甲"
