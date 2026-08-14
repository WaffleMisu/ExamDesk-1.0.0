from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class SimilarReviewItem:
    attempt_id: str
    candidate_name: str
    question_id: str
    blank_index: int
    response: str
    accepted_answer: str
    similarity: float
    score_if_accepted: Decimal


class ReviewService:
    def __init__(self, database) -> None:
        self.database = database

    def list_pending_similar_answers(self, session_id: str) -> list[SimilarReviewItem]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT aa.attempt_id, a.candidate_name, aa.question_id, aa.similar_flags_json
                FROM attempt_answers aa
                JOIN attempts a ON a.id = aa.attempt_id
                WHERE a.session_id = ? AND a.is_void = 0 AND aa.similar_flags_json != '[]'
                ORDER BY a.candidate_name, aa.display_order
                """,
                (session_id,),
            ).fetchall()
            reviews = connection.execute(
                """
                SELECT attempt_id, question_id, blank_index, decision
                FROM score_reviews WHERE attempt_id IN (
                    SELECT id FROM attempts WHERE session_id = ?
                ) ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
        decided = {
            (row["attempt_id"], row["question_id"], row["blank_index"]): row["decision"]
            for row in reviews
        }
        result = []
        for row in rows:
            for flag in json.loads(row["similar_flags_json"]):
                key = (row["attempt_id"], row["question_id"], int(flag["blank_index"]))
                if key in decided:
                    continue
                result.append(
                    SimilarReviewItem(
                        attempt_id=row["attempt_id"],
                        candidate_name=row["candidate_name"],
                        question_id=row["question_id"],
                        blank_index=int(flag["blank_index"]),
                        response=str(flag["response"]),
                        accepted_answer=str(flag["accepted_answer"]),
                        similarity=float(flag["similarity"]),
                        score_if_accepted=Decimal(str(flag["estimated_score"])),
                    )
                )
        return result

    def review_similar_answer(
        self,
        *,
        attempt_id: str,
        question_id: str,
        blank_index: int,
        accept: bool,
        reviewer_id: str,
        note: str = "",
    ) -> Decimal:
        decision = "accept" if accept else "reject"
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            answer = connection.execute(
                """
                SELECT strict_score, similar_flags_json FROM attempt_answers
                WHERE attempt_id = ? AND question_id = ?
                """,
                (attempt_id, question_id),
            ).fetchone()
            if answer is None:
                raise KeyError((attempt_id, question_id))
            flags = json.loads(answer["similar_flags_json"])
            target = next(
                (item for item in flags if int(item["blank_index"]) == blank_index),
                None,
            )
            if target is None:
                raise ValueError("该空没有相似答案复核项")
            latest_decisions = {
                row["blank_index"]: row["decision"]
                for row in connection.execute(
                    """
                    SELECT blank_index, decision FROM score_reviews
                    WHERE attempt_id = ? AND question_id = ? ORDER BY created_at
                    """,
                    (attempt_id, question_id),
                ).fetchall()
            }
            latest_decisions[blank_index] = decision
            score_after = Decimal(answer["strict_score"])
            for flag in flags:
                if latest_decisions.get(int(flag["blank_index"])) == "accept":
                    score_after += Decimal(str(flag["estimated_score"])) - Decimal(
                        str(flag["strict_score"])
                    )
            connection.execute(
                """
                INSERT INTO score_reviews(
                    id, attempt_id, question_id, blank_index, decision,
                    score_before, score_after, reviewer_id, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    attempt_id,
                    question_id,
                    blank_index,
                    decision,
                    answer["strict_score"],
                    str(score_after),
                    reviewer_id,
                    note.strip(),
                    now,
                ),
            )
            connection.execute(
                "UPDATE attempt_answers SET final_score = ? WHERE attempt_id = ? AND question_id = ?",
                (str(score_after), attempt_id, question_id),
            )
            final_total = sum(
                (
                    Decimal(row["final_score"] or row["strict_score"])
                    for row in connection.execute(
                        "SELECT strict_score, final_score FROM attempt_answers WHERE attempt_id = ?",
                        (attempt_id,),
                    ).fetchall()
                ),
                Decimal("0"),
            )
            connection.execute(
                "UPDATE attempts SET final_score = ? WHERE id = ?",
                (str(final_total), attempt_id),
            )
        return final_total

    def void_attempt(self, attempt_id: str, *, reason: str, actor_id: str) -> None:
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise ValueError("作废原因不能为空")
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """
                UPDATE attempts SET status = 'void', is_void = 1, void_reason = ?
                WHERE id = ? AND is_void = 0
                """,
                (cleaned_reason, attempt_id),
            ).rowcount
            if not changed:
                raise ValueError("答题记录不存在或已经作废")
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, actor_id, action, entity_type, entity_id, details_json, created_at
                ) VALUES (?, ?, 'void_attempt', 'attempt', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    actor_id,
                    attempt_id,
                    json.dumps({"reason": cleaned_reason}, ensure_ascii=False),
                    now,
                ),
            )

