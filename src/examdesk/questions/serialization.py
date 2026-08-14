from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from examdesk.domain.enums import MatchMode, QuestionStatus, QuestionType, UsageScope
from examdesk.domain.models import (
    BlankDefinition,
    QuestionDraft,
    QuestionOption,
    UnorderedGroup,
)


def question_to_payload(
    question: QuestionDraft,
    asset_sha_by_id: dict[str, str] | None = None,
) -> dict:
    asset_map = asset_sha_by_id or {}
    return {
        "id": question.id,
        "display_number": question.display_number,
        "question_type": question.question_type.value,
        "stem": question.stem,
        "basis": question.basis,
        "status": question.status.value,
        "usage_scope": question.usage_scope.value,
        "applicable_year": question.applicable_year,
        "source": question.source,
        "chapter": question.chapter,
        "clause": question.clause,
        "difficulty": question.difficulty,
        "tags": list(question.tags),
        "score": str(question.score),
        "correct_option_keys": sorted(question.correct_option_keys),
        "options": [
            {
                "key": option.key,
                "text": option.text,
                "asset_refs": [asset_map.get(asset_id, asset_id) for asset_id in option.asset_ids],
            }
            for option in question.options
        ],
        "blanks": [
            {
                "index": blank.index,
                "accepted_answers": list(blank.accepted_answers),
                "score": str(blank.score),
                "match_mode": blank.match_mode.value,
            }
            for blank in question.blanks
        ],
        "unordered_groups": [list(group.indexes) for group in question.unordered_groups],
        "question_asset_refs": [
            asset_map.get(asset_id, asset_id) for asset_id in question.question_asset_ids
        ],
    }


def question_from_payload(
    payload: dict,
    asset_id_by_ref: dict[str, str] | None = None,
) -> QuestionDraft:
    asset_map = asset_id_by_ref or {}
    return QuestionDraft(
        id=str(payload["id"]),
        display_number=str(payload.get("display_number", "")),
        question_type=QuestionType(payload["question_type"]),
        stem=str(payload["stem"]),
        basis=str(payload.get("basis", "")),
        status=QuestionStatus(payload.get("status", QuestionStatus.DRAFT.value)),
        usage_scope=UsageScope(payload.get("usage_scope", UsageScope.BOTH.value)),
        applicable_year=payload.get("applicable_year"),
        source=str(payload.get("source", "")),
        chapter=str(payload.get("chapter", "")),
        clause=str(payload.get("clause", "")),
        difficulty=str(payload.get("difficulty", "")),
        tags=[str(value) for value in payload.get("tags", [])],
        score=Decimal(str(payload.get("score", "0"))),
        correct_option_keys={str(value) for value in payload.get("correct_option_keys", [])},
        options=[
            QuestionOption(
                str(item["key"]),
                str(item.get("text", "")),
                tuple(asset_map.get(str(ref), str(ref)) for ref in item.get("asset_refs", [])),
            )
            for item in payload.get("options", [])
        ],
        blanks=[
            BlankDefinition(
                int(item["index"]),
                tuple(str(value) for value in item.get("accepted_answers", [])),
                Decimal(str(item["score"])),
                MatchMode(item.get("match_mode", MatchMode.TEXT_SIMILARITY.value)),
            )
            for item in payload.get("blanks", [])
        ],
        unordered_groups=[
            UnorderedGroup(tuple(int(index) for index in indexes))
            for indexes in payload.get("unordered_groups", [])
        ],
        question_asset_ids=[
            asset_map.get(str(ref), str(ref)) for ref in payload.get("question_asset_refs", [])
        ],
    )


def question_payload_hash(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

