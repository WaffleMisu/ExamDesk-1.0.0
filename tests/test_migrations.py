import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from examdesk.db.connection import Database
from examdesk.db.migrations import initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import BlankDefinition, QuestionDraft
from examdesk.questions import QuestionRepository


def test_initial_migration_creates_core_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "admin.sqlite3"

    assert initialize_database(database_path) == 7
    assert initialize_database(database_path) == 7

    with Database(database_path).connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        migration_count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

    assert migration_count == 7
    assert {
        "administrators",
        "questions",
        "question_versions",
        "sessions",
        "attempts",
        "attempt_answers",
        "foreground_events",
        "score_reviews",
        "audit_events",
        "admin_work_authorizations",
    }.issubset(tables)

    with Database(database_path).connect() as connection:
        session_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
    assert {"similarity_level", "custom_similarity_threshold"} <= session_columns

    with Database(database_path).connect() as connection:
        attempt_columns = {row["name"] for row in connection.execute("PRAGMA table_info(attempts)").fetchall()}
    assert {"package_id", "state_filename", "state_error"} <= attempt_columns

    with Database(database_path).connect() as connection:
        version_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(question_versions)").fetchall()
        }
        fingerprint_schema = connection.execute(
            "SELECT value_json FROM app_settings WHERE key = 'question_fingerprint_schema'"
        ).fetchone()["value_json"]
    assert {"surface_hash", "answer_hash", "content_hash"} <= version_columns
    assert fingerprint_schema == "2"


def test_database_context_manager_closes_connection(tmp_path: Path) -> None:
    database_path = tmp_path / "closing.sqlite3"
    initialize_database(database_path)
    with Database(database_path).connect() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_fingerprint_refresh_repairs_existing_question_versions(tmp_path: Path) -> None:
    database_path = tmp_path / "fingerprint-refresh.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    question = QuestionDraft(
        question_type=QuestionType.FILL,
        stem="当前操作类别为_____",
        basis="",
        status=QuestionStatus.ENABLED,
        blanks=[BlankDefinition(1, ("0102",), Decimal("1"))],
        score=Decimal("1"),
    )
    repository.create(question, actor_id=None)
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE question_versions SET surface_hash = '', answer_hash = '', content_hash = 'old'"
        )
        connection.execute("DELETE FROM app_settings WHERE key = 'question_fingerprint_schema'")

    assert initialize_database(database_path) == 7

    expected = repository.fingerprints(question)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT surface_hash, answer_hash, content_hash FROM question_versions"
        ).fetchone()
    assert (row["surface_hash"], row["answer_hash"], row["content_hash"]) == (
        expected.surface,
        expected.answer,
        expected.content,
    )
