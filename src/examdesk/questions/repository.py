from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from examdesk.domain.enums import MatchMode, QuestionStatus, QuestionType, UsageScope
from examdesk.domain.models import (
    BlankDefinition,
    QuestionDraft,
    QuestionOption,
    UnorderedGroup,
)
from examdesk.domain.question_fingerprints import (
    QuestionFingerprints,
    build_question_fingerprints,
    normalize_duplicate_text,
)

from .validation import ValidationIssue, validate_question


class QuestionValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__("question validation failed")
        self.issues = issues


class QuestionVersionConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SavedQuestion:
    id: str
    version: int
    duplicate_key: str
    bank_revision: int = 0


@dataclass(frozen=True, slots=True)
class QuestionQuery:
    keyword: str = ""
    question_type: QuestionType | None = None
    status: QuestionStatus | None = None
    usage_scope: UsageScope | None = None
    applicable_year: int | None = None
    chapter: str = ""
    clause: str = ""
    source: str = ""
    difficulty: str = ""
    tags: tuple[str, ...] = ()
    duplicate_only: bool = False
    sort_by: str = "display_number"
    sort_descending: bool = False


@dataclass(frozen=True, slots=True)
class QuestionListItem:
    id: str
    version: int
    display_number: str
    question_type: QuestionType
    stem: str
    status: QuestionStatus
    usage_scope: UsageScope
    applicable_year: int | None
    source: str
    chapter: str
    clause: str
    difficulty: str
    tags: tuple[str, ...]
    score: Decimal


@dataclass(frozen=True, slots=True)
class QuestionPage:
    items: tuple[QuestionListItem, ...]
    total: int
    page: int
    page_size: int


class QuestionRepository:
    def __init__(self, database) -> None:
        self.database = database

    def create(self, draft: QuestionDraft, actor_id: str | None) -> SavedQuestion:
        issues = validate_question(draft)
        if issues:
            raise QuestionValidationError(issues)
        now = datetime.now(UTC).isoformat()
        fingerprints = self.fingerprints(draft)
        version_id = str(uuid4())
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO questions(
                    id, display_number, question_type, status, usage_scope,
                    applicable_year, source, chapter, clause, difficulty,
                    tags_json, current_version, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.display_number,
                    draft.question_type.value,
                    draft.status.value,
                    draft.usage_scope.value,
                    draft.applicable_year,
                    draft.source,
                    draft.chapter,
                    draft.clause,
                    draft.difficulty,
                    _json(sorted(set(draft.tags))),
                    actor_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO question_versions(
                    id, question_id, version, stem, basis, options_json,
                    answer_json, scoring_json, surface_hash, answer_hash,
                    content_hash, created_by, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    draft.id,
                    draft.stem,
                    draft.basis,
                    _json(_options_payload(draft)),
                    _json(_answer_payload(draft)),
                    _json(_scoring_payload(draft)),
                    fingerprints.surface,
                    fingerprints.answer,
                    fingerprints.content,
                    actor_id,
                    now,
                ),
            )
            _insert_asset_links(connection, draft, 1)
            bank_revision = _bump_bank_revision(connection, now)
        return SavedQuestion(draft.id, 1, fingerprints.content, bank_revision)

    def update(
        self,
        draft: QuestionDraft,
        actor_id: str | None,
        *,
        expected_version: int,
    ) -> SavedQuestion:
        issues = validate_question(draft)
        if issues:
            raise QuestionValidationError(issues)
        now = datetime.now(UTC).isoformat()
        fingerprints = self.fingerprints(draft)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT current_version FROM questions WHERE id = ?",
                (draft.id,),
            ).fetchone()
            if row is None:
                raise KeyError(draft.id)
            current_version = int(row["current_version"])
            if current_version != expected_version:
                raise QuestionVersionConflict(
                    f"question changed from version {expected_version} to {current_version}"
                )
            new_version = current_version + 1
            connection.execute(
                """
                UPDATE questions SET
                    display_number = ?, question_type = ?, status = ?, usage_scope = ?,
                    applicable_year = ?, source = ?, chapter = ?, clause = ?, difficulty = ?,
                    tags_json = ?, current_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    draft.display_number,
                    draft.question_type.value,
                    draft.status.value,
                    draft.usage_scope.value,
                    draft.applicable_year,
                    draft.source,
                    draft.chapter,
                    draft.clause,
                    draft.difficulty,
                    _json(sorted(set(draft.tags))),
                    new_version,
                    now,
                    draft.id,
                ),
            )
            connection.execute(
                """
                INSERT INTO question_versions(
                    id, question_id, version, stem, basis, options_json,
                    answer_json, scoring_json, surface_hash, answer_hash,
                    content_hash, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    draft.id,
                    new_version,
                    draft.stem,
                    draft.basis,
                    _json(_options_payload(draft)),
                    _json(_answer_payload(draft)),
                    _json(_scoring_payload(draft)),
                    fingerprints.surface,
                    fingerprints.answer,
                    fingerprints.content,
                    actor_id,
                    now,
                ),
            )
            _insert_asset_links(connection, draft, new_version)
            bank_revision = _bump_bank_revision(connection, now)
        return SavedQuestion(draft.id, new_version, fingerprints.content, bank_revision)

    def get(self, question_id: str, version: int | None = None) -> QuestionDraft:
        with self.database.connect() as connection:
            metadata = connection.execute(
                "SELECT * FROM questions WHERE id = ?",
                (question_id,),
            ).fetchone()
            if metadata is None:
                raise KeyError(question_id)
            selected_version = version or int(metadata["current_version"])
            content = connection.execute(
                "SELECT * FROM question_versions WHERE question_id = ? AND version = ?",
                (question_id, selected_version),
            ).fetchone()
            if content is None:
                raise KeyError((question_id, selected_version))
            asset_rows = connection.execute(
                """
                SELECT owner_kind, owner_key, asset_id FROM question_asset_links
                WHERE question_id = ? AND question_version = ? ORDER BY sort_order
                """,
                (question_id, selected_version),
            ).fetchall()
        return _deserialize_question(metadata, content, asset_rows)

    def find_exact_duplicates(self, draft: QuestionDraft) -> list[SavedQuestion]:
        duplicate_key = self.fingerprints(draft).content
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT qv.question_id, qv.version, qv.content_hash
                FROM question_versions qv
                JOIN questions q
                  ON q.id = qv.question_id AND q.current_version = qv.version
                WHERE qv.content_hash = ? ORDER BY q.created_at
                """,
                (duplicate_key,),
            ).fetchall()
        return [SavedQuestion(row["question_id"], row["version"], row["content_hash"]) for row in rows]

    def find_surface_conflicts(self, draft: QuestionDraft) -> list[SavedQuestion]:
        fingerprints = self.fingerprints(draft)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT qv.question_id, qv.version, qv.content_hash
                FROM question_versions qv
                JOIN questions q
                  ON q.id = qv.question_id AND q.current_version = qv.version
                WHERE qv.surface_hash = ? AND qv.content_hash != ?
                ORDER BY q.created_at
                """,
                (fingerprints.surface, fingerprints.content),
            ).fetchall()
        return [SavedQuestion(row["question_id"], row["version"], row["content_hash"]) for row in rows]

    def find_answer_conflicts(self, draft: QuestionDraft) -> list[SavedQuestion]:
        fingerprints = self.fingerprints(draft)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT qv.question_id, qv.version, qv.content_hash
                FROM question_versions qv
                JOIN questions q
                  ON q.id = qv.question_id AND q.current_version = qv.version
                WHERE qv.surface_hash = ? AND qv.answer_hash != ?
                ORDER BY q.created_at
                """,
                (fingerprints.surface, fingerprints.answer),
            ).fetchall()
        return [SavedQuestion(row["question_id"], row["version"], row["content_hash"]) for row in rows]

    def fingerprints(self, draft: QuestionDraft) -> QuestionFingerprints:
        asset_ids = list(draft.question_asset_ids)
        asset_ids.extend(asset_id for option in draft.options for asset_id in option.asset_ids)
        digest_by_id: dict[str, str] = {}
        if asset_ids:
            unique_ids = tuple(dict.fromkeys(asset_ids))
            marks = ",".join("?" for _ in unique_ids)
            with self.database.connect() as connection:
                rows = connection.execute(
                    f"SELECT id, sha256 FROM assets WHERE id IN ({marks})",
                    unique_ids,
                ).fetchall()
            digest_by_id = {row["id"]: row["sha256"] for row in rows}
            missing = [asset_id for asset_id in unique_ids if asset_id not in digest_by_id]
            if missing:
                raise ValueError("题目引用了不存在的图片资源")
        return build_question_fingerprints(
            question_type=draft.question_type.value,
            stem=draft.stem,
            options=_options_payload(draft),
            answer=_answer_payload(draft),
            scoring=_scoring_payload(draft),
            question_assets=[digest_by_id[asset_id] for asset_id in draft.question_asset_ids],
            option_assets={
                option.key.upper(): [digest_by_id[asset_id] for asset_id in option.asset_ids]
                for option in draft.options
            },
        )

    def find_by_display_number(self, display_number: str) -> list[tuple[str, int]]:
        cleaned = display_number.strip()
        if not cleaned:
            return []
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, current_version FROM questions WHERE display_number = ? ORDER BY created_at",
                (cleaned,),
            ).fetchall()
        return [(row["id"], int(row["current_version"])) for row in rows]

    def list_current(self) -> list[tuple[QuestionDraft, int]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, current_version FROM questions ORDER BY display_number, created_at"
            ).fetchall()
        return [(self.get(row["id"]), int(row["current_version"])) for row in rows]

    def list_filtered(self, query: QuestionQuery, *, page: int = 1, page_size: int = 100) -> QuestionPage:
        if page < 1:
            raise ValueError("页码必须大于0")
        if page_size not in (50, 100, 200):
            raise ValueError("每页数量必须是50、100或200")
        where, parameters = _question_filter(query)
        base = """
            FROM questions q
            JOIN question_versions qv
              ON qv.question_id = q.id AND qv.version = q.current_version
        """
        with self.database.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) " + base + where, parameters).fetchone()[0])
            rows = connection.execute(
                """
                SELECT q.id, q.current_version, q.display_number, q.question_type,
                       q.status, q.usage_scope, q.applicable_year, q.source,
                       q.chapter, q.clause, q.difficulty, q.tags_json, qv.stem
                       , qv.scoring_json
                """
                + base
                + where
                + _question_order(query)
                + " LIMIT ? OFFSET ?",
                (*parameters, page_size, (page - 1) * page_size),
            ).fetchall()
        items = tuple(_list_item(row) for row in rows)
        return QuestionPage(items, total, page, page_size)

    def filtered_ids(self, query: QuestionQuery) -> tuple[str, ...]:
        where, parameters = _question_filter(query)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT q.id FROM questions q
                JOIN question_versions qv
                  ON qv.question_id = q.id AND qv.version = q.current_version
                """
                + where
                + _question_order(query),
                parameters,
            ).fetchall()
        return tuple(row["id"] for row in rows)

    def distinct_metadata(self, field: str) -> tuple[str, ...]:
        allowed = {"source", "chapter", "clause", "difficulty"}
        if field not in allowed:
            raise ValueError("不支持的元数据字段")
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT {field} AS value FROM questions WHERE {field} != '' ORDER BY value"
            ).fetchall()
        return tuple(row["value"] for row in rows)

    def bank_revision(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = 'bank_revision'"
            ).fetchone()
        return int(json.loads(row["value_json"])) if row is not None else 0


def question_duplicate_key(draft: QuestionDraft) -> str:
    return build_question_fingerprints(
        question_type=draft.question_type.value,
        stem=draft.stem,
        options=_options_payload(draft),
        answer=_answer_payload(draft),
        scoring=_scoring_payload(draft),
        question_assets=list(draft.question_asset_ids),
        option_assets={option.key.upper(): list(option.asset_ids) for option in draft.options},
    ).content


def _question_filter(query: QuestionQuery) -> tuple[str, tuple]:
    clauses = []
    parameters: list[object] = []
    if query.keyword.strip():
        keyword = "%" + _like_value(query.keyword.strip()) + "%"
        clauses.append(
            "(q.display_number LIKE ? ESCAPE '\\' OR qv.stem LIKE ? ESCAPE '\\' "
            "OR qv.basis LIKE ? ESCAPE '\\' OR q.source LIKE ? ESCAPE '\\')"
        )
        parameters.extend([keyword] * 4)
    for column, value in (
        ("question_type", query.question_type),
        ("status", query.status),
        ("usage_scope", query.usage_scope),
    ):
        if value is not None:
            clauses.append(f"q.{column} = ?")
            parameters.append(value.value)
    if query.applicable_year is not None:
        clauses.append("q.applicable_year = ?")
        parameters.append(query.applicable_year)
    for column, value in (
        ("chapter", query.chapter),
        ("clause", query.clause),
        ("source", query.source),
        ("difficulty", query.difficulty),
    ):
        if value.strip():
            clauses.append(f"q.{column} = ?")
            parameters.append(value.strip())
    tags = tuple(dict.fromkeys(tag.strip() for tag in query.tags if tag.strip()))
    if tags:
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(q.tags_json) WHERE value IN ("
            + ",".join("?" for _ in tags)
            + "))"
        )
        parameters.extend(tags)
    if query.duplicate_only:
        clauses.append(
            "(q.display_number != '' AND EXISTS ("
            "SELECT 1 FROM questions q2 WHERE q2.display_number = q.display_number AND q2.id != q.id"
            ")) OR EXISTS ("
            "SELECT 1 FROM question_versions qv2 JOIN questions q2 ON q2.id = qv2.question_id "
            "WHERE qv2.version = q2.current_version AND qv2.surface_hash = qv.surface_hash AND q2.id != q.id"
            ")"
        )
    return (" WHERE " + " AND ".join(f"({clause})" for clause in clauses) if clauses else "", tuple(parameters))


def _question_order(query: QuestionQuery) -> str:
    score_expression = (
        "CAST(COALESCE(json_extract(qv.scoring_json, '$.score'), "
        "(SELECT SUM(CAST(value AS REAL)) "
        "FROM json_each(json_extract(qv.scoring_json, '$.blank_scores'))), 0) AS REAL)"
    )
    expressions = {
        "display_number": "q.display_number COLLATE NATURAL_NOCASE",
        "question_type": "q.question_type",
        "stem": "qv.stem COLLATE NOCASE",
        "status": "q.status",
        "usage_scope": "q.usage_scope",
        "applicable_year": "COALESCE(q.applicable_year, 0)",
        "chapter": "q.chapter COLLATE NOCASE",
        "tags": "q.tags_json COLLATE NOCASE",
        "difficulty": "q.difficulty COLLATE NOCASE",
        "score": score_expression,
    }
    expression = expressions.get(query.sort_by, expressions["display_number"])
    direction = "DESC" if query.sort_descending else "ASC"
    return f" ORDER BY {expression} {direction}, q.display_number COLLATE NATURAL_NOCASE, q.created_at"


def _like_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _list_item(row) -> QuestionListItem:
    return QuestionListItem(
        id=row["id"],
        version=int(row["current_version"]),
        display_number=row["display_number"],
        question_type=QuestionType(row["question_type"]),
        stem=row["stem"],
        status=QuestionStatus(row["status"]),
        usage_scope=UsageScope(row["usage_scope"]),
        applicable_year=row["applicable_year"],
        source=row["source"],
        chapter=row["chapter"],
        clause=row["clause"],
        difficulty=row["difficulty"],
        tags=tuple(json.loads(row["tags_json"])),
        score=_score_from_json(row["scoring_json"]),
    )


def _score_from_json(value: str) -> Decimal:
    payload = json.loads(value)
    if "score" in payload:
        return Decimal(payload["score"])
    return sum((Decimal(item) for item in payload.get("blank_scores", [])), Decimal("0"))


def _duplicate_normalize(value: str) -> str:
    return normalize_duplicate_text(value)


def _options_payload(draft: QuestionDraft) -> list[dict]:
    return [
        {
            "key": option.key.upper(),
            "text": option.text,
            "asset_ids": list(option.asset_ids),
        }
        for option in draft.options
    ]


def _answer_payload(draft: QuestionDraft) -> dict:
    if draft.question_type is not QuestionType.FILL:
        return {"correct_option_keys": sorted(draft.correct_option_keys)}
    return {
        "blanks": [
            {
                "index": blank.index,
                "accepted_answers": list(blank.accepted_answers),
                "match_mode": blank.match_mode.value,
            }
            for blank in draft.blanks
        ],
        "unordered_groups": [list(group.indexes) for group in draft.unordered_groups],
    }


def _scoring_payload(draft: QuestionDraft) -> dict:
    if draft.question_type is not QuestionType.FILL:
        return {"score": str(draft.score)}
    return {"blank_scores": [str(blank.score) for blank in draft.blanks]}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _insert_asset_links(connection, draft: QuestionDraft, version: int) -> None:
    rows = []
    for sort_order, asset_id in enumerate(draft.question_asset_ids):
        rows.append((draft.id, version, "stem", "", asset_id, sort_order))
    for option in draft.options:
        for sort_order, asset_id in enumerate(option.asset_ids):
            rows.append((draft.id, version, "option", option.key.upper(), asset_id, sort_order))
    connection.executemany(
        """
        INSERT INTO question_asset_links(
            question_id, question_version, owner_kind, owner_key, asset_id, sort_order
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _deserialize_question(metadata, content, asset_rows) -> QuestionDraft:
    question_type = QuestionType(metadata["question_type"])
    options_payload = json.loads(content["options_json"])
    answer_payload = json.loads(content["answer_json"])
    scoring_payload = json.loads(content["scoring_json"])
    assets_by_owner: dict[str, list[str]] = {}
    for row in asset_rows:
        owner = "stem" if row["owner_kind"] == "stem" else row["owner_key"]
        assets_by_owner.setdefault(owner, []).append(row["asset_id"])
    options = [
        QuestionOption(
            item["key"],
            item["text"],
            tuple(assets_by_owner.get(item["key"], item.get("asset_ids", []))),
        )
        for item in options_payload
    ]
    blanks = []
    groups = []
    correct_keys: set[str] = set()
    score = Decimal("0")
    if question_type is QuestionType.FILL:
        blank_scores = scoring_payload["blank_scores"]
        blanks = [
            BlankDefinition(
                index=item["index"],
                accepted_answers=tuple(item["accepted_answers"]),
                score=Decimal(blank_scores[position]),
                match_mode=MatchMode(item["match_mode"]),
            )
            for position, item in enumerate(answer_payload["blanks"])
        ]
        groups = [UnorderedGroup(tuple(indexes)) for indexes in answer_payload["unordered_groups"]]
        score = sum((blank.score for blank in blanks), Decimal("0"))
    else:
        correct_keys = set(answer_payload["correct_option_keys"])
        score = Decimal(scoring_payload["score"])
    return QuestionDraft(
        id=metadata["id"],
        question_type=question_type,
        stem=content["stem"],
        basis=content["basis"],
        display_number=metadata["display_number"],
        status=QuestionStatus(metadata["status"]),
        usage_scope=UsageScope(metadata["usage_scope"]),
        applicable_year=metadata["applicable_year"],
        source=metadata["source"],
        chapter=metadata["chapter"],
        clause=metadata["clause"],
        difficulty=metadata["difficulty"],
        tags=json.loads(metadata["tags_json"]),
        options=options,
        correct_option_keys=correct_keys,
        blanks=blanks,
        unordered_groups=groups,
        question_asset_ids=assets_by_owner.get("stem", []),
        score=score,
    )


def _bump_bank_revision(connection, now: str) -> int:
    row = connection.execute(
        "SELECT value_json FROM app_settings WHERE key = 'bank_revision'"
    ).fetchone()
    revision = (int(json.loads(row["value_json"])) if row is not None else 0) + 1
    connection.execute(
        """
        INSERT INTO app_settings(key, value_json, updated_at) VALUES ('bank_revision', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (_json(revision), now),
    )
    return revision
