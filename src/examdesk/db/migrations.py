from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from examdesk.domain.question_fingerprints import build_question_fingerprints

from .connection import Database

MIGRATION_001 = r"""
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE administrators (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('supervisor', 'admin')),
    password_digest TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    auth_generation INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE questions (
    id TEXT PRIMARY KEY,
    display_number TEXT NOT NULL DEFAULT '',
    question_type TEXT NOT NULL CHECK (question_type IN ('single', 'multiple', 'judge', 'fill')),
    status TEXT NOT NULL CHECK (status IN ('draft', 'enabled', 'disabled')),
    usage_scope TEXT NOT NULL CHECK (usage_scope IN ('practice_only', 'exam_only', 'both')),
    applicable_year INTEGER,
    source TEXT NOT NULL DEFAULT '',
    chapter TEXT NOT NULL DEFAULT '',
    clause TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE question_versions (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    stem TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT '',
    options_json TEXT NOT NULL DEFAULT '[]',
    answer_json TEXT NOT NULL,
    scoring_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (question_id, version)
);

CREATE INDEX idx_question_versions_hash ON question_versions(content_hash);
CREATE INDEX idx_questions_filter ON questions(status, usage_scope, applicable_year, question_type);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    perceptual_hash TEXT,
    media_type TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    width INTEGER,
    height INTEGER,
    byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE question_asset_links (
    question_id TEXT NOT NULL,
    question_version INTEGER NOT NULL,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('stem', 'option')),
    owner_key TEXT NOT NULL DEFAULT '',
    asset_id TEXT NOT NULL REFERENCES assets(id),
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (question_id, question_version, owner_kind, owner_key, asset_id),
    FOREIGN KEY (question_id, question_version)
        REFERENCES question_versions(question_id, version) ON DELETE CASCADE
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('draft', 'locked', 'archived')),
    password_digest TEXT NOT NULL,
    filters_json TEXT NOT NULL DEFAULT '{}',
    question_counts_json TEXT NOT NULL DEFAULT '{}',
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
    roster_required INTEGER NOT NULL DEFAULT 0 CHECK (roster_required IN (0, 1)),
    monitoring_enabled INTEGER NOT NULL DEFAULT 0 CHECK (monitoring_enabled IN (0, 1)),
    duration_minutes INTEGER CHECK (duration_minutes IS NULL OR duration_minutes > 0),
    review_policy TEXT NOT NULL CHECK (review_policy IN ('immediate', 'after_release', 'score_only')),
    review_release_at TEXT,
    min_software_version TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    locked_at TEXT
);

CREATE TABLE session_questions (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    question_version INTEGER NOT NULL,
    base_order INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (session_id, question_id)
);

CREATE TABLE session_roster (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL COLLATE NOCASE,
    department TEXT NOT NULL DEFAULT '',
    extra_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (session_id, display_name)
);

CREATE TABLE attempts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    candidate_name TEXT NOT NULL,
    machine_name TEXT NOT NULL,
    windows_user TEXT NOT NULL,
    software_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'submitted', 'incomplete', 'void')),
    started_at TEXT NOT NULL,
    deadline_at TEXT,
    submitted_at TEXT,
    submit_reason TEXT CHECK (submit_reason IN ('manual', 'timeout', 'recovered_timeout')),
    strict_score TEXT,
    estimated_score TEXT,
    final_score TEXT,
    max_score TEXT NOT NULL,
    question_order_json TEXT NOT NULL,
    monitor_status TEXT NOT NULL DEFAULT 'not_started',
    source_file_hash TEXT,
    imported_at TEXT,
    is_void INTEGER NOT NULL DEFAULT 0 CHECK (is_void IN (0, 1)),
    void_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_attempts_session_name ON attempts(session_id, candidate_name);
CREATE UNIQUE INDEX idx_attempts_source_hash
    ON attempts(source_file_hash) WHERE source_file_hash IS NOT NULL;

CREATE TABLE attempt_answers (
    attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    option_order_json TEXT NOT NULL DEFAULT '[]',
    response_json TEXT NOT NULL,
    strict_score TEXT NOT NULL,
    estimated_score TEXT NOT NULL,
    final_score TEXT,
    similar_flags_json TEXT NOT NULL DEFAULT '[]',
    answered_at TEXT,
    PRIMARY KEY (attempt_id, question_id)
);

CREATE TABLE foreground_events (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds REAL,
    application_name TEXT NOT NULL DEFAULT '',
    process_name TEXT NOT NULL DEFAULT '',
    window_title TEXT NOT NULL DEFAULT '',
    event_kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_foreground_events_attempt ON foreground_events(attempt_id, started_at);

CREATE TABLE score_reviews (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES attempts(id),
    question_id TEXT NOT NULL,
    blank_index INTEGER,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'reject')),
    score_before TEXT NOT NULL,
    score_after TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE practice_progress (
    bank_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    last_answered_at TEXT,
    PRIMARY KEY (bank_id, question_id)
);

CREATE TABLE package_imports (
    id TEXT PRIMARY KEY,
    package_kind TEXT NOT NULL,
    package_id TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    imported_by TEXT,
    imported_at TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_events_time ON audit_events(created_at);
"""

MIGRATION_002 = r"""
CREATE TABLE admin_work_authorizations (
    id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL REFERENCES administrators(id),
    bank_id TEXT NOT NULL,
    patch_secret BLOB NOT NULL,
    base_revision INTEGER NOT NULL,
    auth_generation INTEGER NOT NULL,
    issued_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX idx_admin_work_active
    ON admin_work_authorizations(admin_id, revoked_at);
"""

MIGRATION_003 = r"""
ALTER TABLE sessions ADD COLUMN random_seed TEXT NOT NULL DEFAULT '';
ALTER TABLE sessions ADD COLUMN session_auth_key BLOB;
ALTER TABLE sessions ADD COLUMN package_id TEXT;
"""

MIGRATION_004 = r"""
ALTER TABLE attempts ADD COLUMN time_anomaly INTEGER NOT NULL DEFAULT 0;
ALTER TABLE attempt_answers ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '{}';
"""

MIGRATION_005 = r"""
ALTER TABLE sessions ADD COLUMN similarity_level TEXT NOT NULL DEFAULT 'standard'
    CHECK (similarity_level IN ('strict', 'standard', 'loose', 'custom'));
ALTER TABLE sessions ADD COLUMN custom_similarity_threshold REAL
    CHECK (custom_similarity_threshold IS NULL OR
           (custom_similarity_threshold >= 50.0 AND custom_similarity_threshold <= 100.0));
"""

MIGRATION_006 = r"""
ALTER TABLE attempts ADD COLUMN package_id TEXT;
ALTER TABLE attempts ADD COLUMN state_filename TEXT NOT NULL DEFAULT '';
ALTER TABLE attempts ADD COLUMN state_error TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_questions_type ON questions(question_type);
CREATE INDEX idx_questions_status ON questions(status);
CREATE INDEX idx_questions_scope ON questions(usage_scope);
CREATE INDEX idx_questions_year ON questions(applicable_year);
CREATE INDEX idx_questions_updated ON questions(updated_at);
CREATE INDEX idx_sessions_status_created ON sessions(status, created_at);
CREATE INDEX idx_attempts_status_created ON attempts(status, created_at);
"""

MIGRATION_007 = r"""
ALTER TABLE question_versions ADD COLUMN surface_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE question_versions ADD COLUMN answer_hash TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_question_versions_surface ON question_versions(surface_hash);
CREATE INDEX idx_question_versions_answer ON question_versions(answer_hash);
"""

MIGRATIONS = (
    (1, "initial_schema", MIGRATION_001),
    (2, "admin_work_authorizations", MIGRATION_002),
    (3, "session_package_fields", MIGRATION_003),
    (4, "result_snapshot_fields", MIGRATION_004),
    (5, "session_similarity_settings", MIGRATION_005),
    (6, "data_management_fields", MIGRATION_006),
    (7, "question_fingerprints", MIGRATION_007),
)


def initialize_database(path: Path) -> int:
    database = Database(path)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

        for version, name, script in MIGRATIONS:
            if version in applied:
                continue
            escaped_name = name.replace("'", "''")
            migration_sql = (
                "BEGIN IMMEDIATE;\n"
                + script
                + "\nINSERT INTO schema_migrations(version, name) VALUES "
                + f"({version}, '{escaped_name}');\nCOMMIT;"
            )
            try:
                connection.executescript(migration_sql)
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.rollback()
                raise

        _refresh_question_fingerprints(connection)

        row = connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
        return int(row["version"])


def _refresh_question_fingerprints(connection) -> None:
    marker = connection.execute(
        "SELECT value_json FROM app_settings WHERE key = 'question_fingerprint_schema'"
    ).fetchone()
    if marker is not None and json.loads(marker["value_json"]) == 2:
        return

    versions = connection.execute(
        """
        SELECT q.question_type, qv.question_id, qv.version, qv.options_json,
               qv.answer_json, qv.scoring_json, qv.stem
        FROM question_versions qv
        JOIN questions q ON q.id = qv.question_id
        ORDER BY qv.question_id, qv.version
        """
    ).fetchall()
    for version in versions:
        asset_rows = connection.execute(
            """
            SELECT qal.owner_kind, qal.owner_key, a.sha256
            FROM question_asset_links qal
            JOIN assets a ON a.id = qal.asset_id
            WHERE qal.question_id = ? AND qal.question_version = ?
            ORDER BY qal.owner_kind, qal.owner_key, qal.sort_order
            """,
            (version["question_id"], version["version"]),
        ).fetchall()
        question_assets = [
            row["sha256"] for row in asset_rows if row["owner_kind"] == "stem"
        ]
        option_assets: dict[str, list[str]] = {}
        for row in asset_rows:
            if row["owner_kind"] == "option":
                option_assets.setdefault(row["owner_key"].upper(), []).append(row["sha256"])
        fingerprints = build_question_fingerprints(
            question_type=version["question_type"],
            stem=version["stem"],
            options=json.loads(version["options_json"]),
            answer=json.loads(version["answer_json"]),
            scoring=json.loads(version["scoring_json"]),
            question_assets=question_assets,
            option_assets=option_assets,
        )
        connection.execute(
            """
            UPDATE question_versions
            SET surface_hash = ?, answer_hash = ?, content_hash = ?
            WHERE question_id = ? AND version = ?
            """,
            (
                fingerprints.surface,
                fingerprints.answer,
                fingerprints.content,
                version["question_id"],
                version["version"],
            ),
        )

    now = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                                       updated_at = excluded.updated_at
        """,
        ("question_fingerprint_schema", json.dumps(2), now),
    )
