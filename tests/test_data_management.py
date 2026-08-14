import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.maintenance import DataManagementService, OrphanAttemptService
from examdesk.questions import QuestionRepository
from examdesk.security.passwords import hash_secret


def setup_database(tmp_path: Path):
    path = tmp_path / "data.sqlite3"
    initialize_database(path)
    database = Database(path)
    admins = AdminRepository(database)
    supervisor = admins.create_first_admin("主管理员", "supervisor-pass", hash_secret("RECOVERY").encode())
    ordinary = admins.add_admin(supervisor.id, "管理员", "admin-pass")
    return database, admins, supervisor, ordinary


def question(number: str) -> QuestionDraft:
    return QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem=f"题目{number}",
        basis="",
        display_number=number,
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "正确"), QuestionOption("B", "错误")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )


def add_session(database: Database, session_id: str, question_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    with database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO sessions(
                id, name, status, password_digest, min_software_version,
                created_by, created_at, review_policy
            ) VALUES (?, ?, 'locked', ?, '2.2.1', 'admin', ?, 'immediate')
            """,
            (session_id, session_id, hash_secret("600000").encode(), now),
        )
        connection.execute(
            """
            INSERT INTO session_questions(
                session_id, question_id, question_version, base_order, snapshot_json
            ) VALUES (?, ?, 1, 1, '{}')
            """,
            (session_id, question_id),
        )


def add_attempt(database: Database, attempt_id: str, session_id: str, state_filename: str = "") -> None:
    now = datetime.now(UTC).isoformat()
    with database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO attempts(
                id, session_id, candidate_name, machine_name, windows_user,
                software_version, status, started_at, max_score, question_order_json,
                created_at, package_id, state_filename
            ) VALUES (?, ?, '测试用户甲', 'PC', 'user', '2.2.1', 'active', ?, '1', '[]', ?, 'pkg', ?)
            """,
            (attempt_id, session_id, now, now, state_filename),
        )


def test_delete_questions_disables_referenced_and_deletes_unreferenced(tmp_path: Path) -> None:
    database, admins, supervisor, ordinary = setup_database(tmp_path)
    questions = QuestionRepository(database)
    referenced = question("001")
    unreferenced = question("002")
    questions.create(referenced, supervisor.id)
    questions.create(unreferenced, supervisor.id)
    add_session(database, "session-1", referenced.id)
    backups = []
    service = DataManagementService(database)

    with pytest.raises(PermissionError):
        service.delete_questions(
            [referenced.id],
            actor_id=ordinary.id,
            password="admin-pass",
            reason="清理",
            backup=lambda: backups.append(True),
        )

    result = service.delete_questions(
        [referenced.id, unreferenced.id],
        actor_id=supervisor.id,
        password="supervisor-pass",
        reason="题库清理",
        backup=lambda: backups.append(True),
    )

    assert result.deleted_ids == (unreferenced.id,)
    assert result.disabled_ids == (referenced.id,)
    assert backups == [True]
    assert questions.get(referenced.id).status is QuestionStatus.DISABLED
    with pytest.raises(KeyError):
        questions.get(unreferenced.id)
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT action, details_json FROM audit_events ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert audit["action"] == "delete_questions"
    assert json.loads(audit["details_json"])["disabled_ids"] == [referenced.id]


def test_delete_session_archives_when_attempt_exists(tmp_path: Path) -> None:
    database, admins, supervisor, _ordinary = setup_database(tmp_path)
    service = DataManagementService(database)
    add_session(database, "session-1", "question-1")
    add_attempt(database, "attempt-1", "session-1")

    result = service.delete_sessions(
        ["session-1"],
        actor_id=supervisor.id,
        password="supervisor-pass",
        reason="归档旧场次",
        backup=lambda: None,
    )

    assert result.archived_ids == ("session-1",)
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM sessions WHERE id = 'session-1'").fetchone()[0] == "archived"


def test_orphan_attempt_can_be_voided_by_any_active_admin(tmp_path: Path) -> None:
    database, admins, supervisor, ordinary = setup_database(tmp_path)
    add_session(database, "session-1", "question-1")
    add_attempt(database, "attempt-1", "session-1", "active_exam_missing.state")
    service = OrphanAttemptService(database, [tmp_path / "state", tmp_path / "state_backup"])

    orphaned = service.list_unrecoverable()
    assert [(item.id, item.issue) for item in orphaned] == [("attempt-1", "状态文件缺失")]

    service.void(
        "attempt-1",
        actor_id=ordinary.id,
        password="admin-pass",
        reason="状态文件无法恢复",
    )
    with database.connect() as connection:
        row = connection.execute("SELECT status, is_void, void_reason FROM attempts WHERE id = 'attempt-1'").fetchone()
    assert (row["status"], row["is_void"], row["void_reason"]) == ("void", 1, "状态文件无法恢复")


def test_orphan_attempt_keeps_valid_state_file_out_of_list(tmp_path: Path) -> None:
    database, _admins, _supervisor, _ordinary = setup_database(tmp_path)
    add_session(database, "session-1", "question-1")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "active_exam_valid.state").write_bytes(b"EXDKST10" + b"x" * 28)
    add_attempt(database, "attempt-1", "session-1", "active_exam_valid.state")

    assert OrphanAttemptService(database, [state_dir]).list_unrecoverable() == []


def test_orphan_attempt_lists_runtime_decryption_error_even_when_file_exists(tmp_path: Path) -> None:
    database, _admins, _supervisor, _ordinary = setup_database(tmp_path)
    add_session(database, "session-1", "question-1")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "active_exam_error.state").write_bytes(b"EXDKST10" + b"x" * 28)
    add_attempt(database, "attempt-1", "session-1", "active_exam_error.state")
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE attempts SET state_error = 'invalid tag' WHERE id = 'attempt-1'"
        )

    orphaned = OrphanAttemptService(database, [state_dir]).list_unrecoverable()

    assert orphaned[0].issue == "状态文件无法解密或内容损坏"


def test_orphan_attempt_lists_archived_session_even_when_state_file_is_valid(tmp_path: Path) -> None:
    database, _admins, _supervisor, _ordinary = setup_database(tmp_path)
    add_session(database, "session-1", "question-1")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "active_exam_archived.state").write_bytes(b"EXDKST10" + b"x" * 28)
    add_attempt(database, "attempt-1", "session-1", "active_exam_archived.state")
    with database.transaction(immediate=True) as connection:
        connection.execute("UPDATE sessions SET status = 'archived' WHERE id = 'session-1'")

    orphaned = OrphanAttemptService(database, [state_dir]).list_unrecoverable()

    assert [(item.id, item.issue) for item in orphaned] == [("attempt-1", "所属场次已归档")]


def test_active_admin_can_batch_update_question_metadata_and_tags(tmp_path: Path) -> None:
    database, _admins, supervisor, ordinary = setup_database(tmp_path)
    questions = QuestionRepository(database)
    first = question("001")
    first.tags = ["原标签"]
    second = question("002")
    questions.create(first, supervisor.id)
    questions.create(second, supervisor.id)
    service = DataManagementService(database)

    changed = service.batch_update_questions(
        [first.id, second.id],
        actor_id=ordinary.id,
        changes={
            "status": QuestionStatus.DISABLED,
            "usage_scope": "practice_only",
            "applicable_year": 2026,
            "difficulty": "较难",
            "tags": ["重点", "复核"],
        },
        tags_mode="append",
    )

    assert changed == 2
    loaded_first = questions.get(first.id)
    loaded_second = questions.get(second.id)
    assert loaded_first.status is QuestionStatus.DISABLED
    assert loaded_first.applicable_year == 2026
    assert loaded_first.tags == ["原标签", "重点", "复核"]
    assert loaded_second.tags == ["重点", "复核"]
