from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class QuestionFingerprints:
    surface: str
    answer: str
    content: str


def build_question_fingerprints(
    *,
    question_type: str,
    stem: str,
    options: list[dict],
    answer: dict,
    scoring: dict,
    question_assets: list[str] | tuple[str, ...] = (),
    option_assets: dict[str, list[str] | tuple[str, ...]] | None = None,
) -> QuestionFingerprints:
    assets_by_option = option_assets or {}
    surface_payload = {
        "question_type": question_type,
        "stem": normalize_duplicate_text(stem),
        "question_assets": list(question_assets),
        "options": [
            {
                "key": str(option.get("key", "")).upper(),
                "text": normalize_duplicate_text(str(option.get("text", ""))),
                "assets": list(assets_by_option.get(str(option.get("key", "")).upper(), ())),
            }
            for option in sorted(options, key=lambda item: str(item.get("key", "")).upper())
        ],
    }
    answer_payload = _canonical_answer(question_type, answer)
    scoring_payload = _canonical_scoring(scoring)
    surface_hash = _hash(surface_payload)
    answer_hash = _hash(answer_payload)
    content_hash = _hash(
        {
            "surface_hash": surface_hash,
            "answer_hash": answer_hash,
            "scoring": scoring_payload,
        }
    )
    return QuestionFingerprints(surface_hash, answer_hash, content_hash)


def normalize_duplicate_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("P", "Z"))
    )


def _canonical_answer(question_type: str, answer: dict) -> dict:
    if question_type != "fill":
        return {
            "correct_option_keys": sorted(
                str(value).upper() for value in answer.get("correct_option_keys", [])
            )
        }

    blanks = {
        int(item["index"]): {
            "answers": sorted(
                {
                    normalize_duplicate_text(str(value))
                    for value in item.get("accepted_answers", [])
                }
            ),
            "match_mode": str(item.get("match_mode", "text_similarity")),
        }
        for item in answer.get("blanks", [])
    }
    groups = [tuple(sorted(int(index) for index in group)) for group in answer.get("unordered_groups", [])]
    groups.sort()
    grouped_indexes = {index for group in groups for index in group}
    fixed = [
        {"index": index, **blanks[index]}
        for index in sorted(blanks)
        if index not in grouped_indexes
    ]
    unordered = [
        {
            "indexes": list(group),
            "answers": sorted(
                (blanks[index] for index in group),
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            ),
        }
        for group in groups
    ]
    return {"fixed": fixed, "unordered": unordered}


def _canonical_scoring(scoring: dict) -> dict:
    if "score" in scoring:
        return {"score": _decimal_text(scoring["score"])}
    return {
        "blank_scores": [_decimal_text(value) for value in scoring.get("blank_scores", [])]
    }


def _decimal_text(value) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def _hash(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
