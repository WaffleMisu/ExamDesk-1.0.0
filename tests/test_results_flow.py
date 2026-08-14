from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_exam_runtime import exam_definition

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import ReviewPolicy, SubmitReason
from examdesk.exam import ExamStateStore
from examdesk.monitoring import FocusEvent
from examdesk.packages import X25519KeyPair
from examdesk.results import (
    AttemptService,
    ResultImportService,
    ReviewService,
    SubmittedReviewError,
    SubmittedReviewStore,
)
from examdesk.security.passwords import hash_secret


def create_master_session(database: Database, definition) -> None:
    now = datetime.now(UTC).isoformat()
    with database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO sessions(
                id, name, description, status, password_digest, filters_json,
                question_counts_json, max_attempts, roster_required, duration_minutes,
                review_policy, min_software_version, created_by, created_at,
                locked_at, random_seed, session_auth_key, package_id
            ) VALUES (?, ?, '', 'locked', ?, '{}', '{}', ?, 0, ?, ?, '2.0.0',
                      'admin', ?, ?, ?, ?, ?)
            """,
            (
                definition.session_id,
                definition.name,
                hash_secret("600000").encode(),
                definition.max_attempts,
                definition.duration_minutes,
                definition.review_policy.value,
                now,
                now,
                definition.random_seed,
                definition.session_auth_key,
                definition.package_id,
            ),
        )


def test_result_dual_save_import_review_replay_and_void(tmp_path: Path) -> None:
    recipient = X25519KeyPair.generate()
    definition = replace(
        exam_definition(),
        result_recipient_public_key=recipient.public_bytes,
    )

    local_database_path = tmp_path / "candidate" / "data.sqlite3"
    initialize_database(local_database_path)
    local_database = Database(local_database_path)
    state_store = ExamStateStore([tmp_path / "candidate" / "state"], b"k" * 32)
    attempts = AttemptService(local_database, state_store)
    state = attempts.start(
        definition,
        candidate_name="测试用户甲",
        software_version="2.0.0",
        machine_name="PC-01",
        windows_user="test_user",
        now=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
    )
    state.set_response("single", ["A"])
    state.set_response("multiple", ["A", "B"])
    state.set_response("judge", ["A"])
    state.set_response("fill", ["永久基本农用区"])
    event = FocusEvent(
        started_at=state.started_at + timedelta(minutes=2),
        ended_at=state.started_at + timedelta(minutes=2, seconds=4),
        duration_seconds=4,
        process_id=200,
        application_name="通讯工具",
        process_name="通讯工具.exe",
        window_title="文件传输",
        event_kind="window",
    )
    artifact = attempts.finalize(
        definition,
        state,
        reason=SubmitReason.MANUAL,
        foreground_events=[event],
        monitor_status="ok",
        local_result_directory=tmp_path / "candidate" / "results",
        submission_directory=tmp_path / "candidate" / "desktop" / "待提交答题记录",
        submitted_at=datetime(2026, 8, 4, 9, 20, tzinfo=UTC),
    )

    assert artifact.local_backup.is_file()
    assert artifact.local_review.is_file()
    assert artifact.submission_file is not None and artifact.submission_file.is_file()
    assert artifact.grade.strict_score == 3
    assert not state_store.paths[0].exists()

    master_database_path = tmp_path / "master" / "data.sqlite3"
    initialize_database(master_database_path)
    master_database = Database(master_database_path)
    create_master_session(master_database, definition)
    importer = ResultImportService(master_database, recipient)
    imported = importer.import_file(artifact.submission_file, imported_by="admin")
    replayed = importer.import_file(artifact.submission_file, imported_by="admin")

    assert imported.imported
    assert imported.candidate_name == "测试用户甲"
    assert replayed.duplicate_file

    review = ReviewService(master_database)
    pending = review.list_pending_similar_answers(definition.session_id)
    assert len(pending) == 1
    assert pending[0].response == "永久基本农用区"
    final_score = review.review_similar_answer(
        attempt_id=state.attempt_id,
        question_id="fill",
        blank_index=1,
        accept=True,
        reviewer_id="admin",
        note="确认属于可接受表述",
    )
    assert final_score == 5

    review.void_attempt(state.attempt_id, reason="测试作废", actor_id="admin")
    with master_database.connect() as connection:
        row = connection.execute(
            "SELECT is_void, void_reason, strict_score, estimated_score, final_score FROM attempts WHERE id = ?",
            (state.attempt_id,),
        ).fetchone()
    assert row["is_void"] == 1
    assert row["void_reason"] == "测试作废"
    assert Decimal(row["strict_score"]) == Decimal("3")
    assert Decimal(row["estimated_score"]) == Decimal("5")
    assert Decimal(row["final_score"]) == Decimal("5")


def test_submitted_review_obeys_release_policy_and_detects_tampering(tmp_path: Path) -> None:
    release_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    definition = replace(
        exam_definition(),
        review_policy=ReviewPolicy.AFTER_RELEASE,
        review_release_at=release_at,
    )
    store = SubmittedReviewStore(tmp_path / "reviews")
    payload = {
        "attempt_id": "attempt-1",
        "session_id": definition.session_id,
        "package_id": definition.package_id,
        "candidate_name": "测试用户甲",
        "submitted_at": "2026-08-04T10:00:00+00:00",
        "strict_score": "6.5",
        "estimated_score": "7",
        "max_score": "8",
        "questions": [{"question_id": "single", "response": ["A"]}],
    }
    path = store.save(definition, payload)

    locked = store.load_latest(
        definition,
        "测试用户甲",
        now=datetime(2026, 8, 4, 11, 0, tzinfo=UTC),
    )
    assert locked.strict_score == Decimal("6.5")
    assert not locked.details_visible
    assert locked.questions == ()

    released = store.load_latest(
        definition,
        "测试用户甲",
        now=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
    )
    assert released.details_visible
    assert released.questions[0]["question_id"] == "single"

    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(SubmittedReviewError, match="损坏"):
        store.load_latest(definition, "测试用户甲", now=release_at)


def test_submitted_review_score_only_never_exposes_question_details(tmp_path: Path) -> None:
    definition = replace(
        exam_definition(),
        review_policy=ReviewPolicy.SCORE_ONLY,
        review_release_at=None,
    )
    store = SubmittedReviewStore(tmp_path / "reviews")
    store.save(
        definition,
        {
            "attempt_id": "attempt-2",
            "session_id": definition.session_id,
            "package_id": definition.package_id,
            "candidate_name": "测试用户甲",
            "submitted_at": "2026-08-04T10:00:00+00:00",
            "strict_score": "6",
            "estimated_score": "7",
            "max_score": "8",
            "questions": [{"question_id": "single", "response": ["A"]}],
        },
    )

    review = store.load_latest(definition, "测试用户甲", now=datetime(2030, 1, 1, tzinfo=UTC))
    assert review.strict_score == Decimal("6")
    assert review.policy is ReviewPolicy.SCORE_ONLY
    assert not review.details_visible
    assert review.questions == ()
