from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from examdesk.db import AdminRepository
from examdesk.domain.enums import AdminRole, QuestionStatus
from examdesk.domain.models import QuestionOption
from examdesk.questions import AssetManager, QuestionRepository, SavedQuestion

from .models import ImportCandidate


@dataclass(slots=True)
class CommitResult:
    saved: list[SavedQuestion] = field(default_factory=list)
    skipped_exact_duplicates: list[str] = field(default_factory=list)
    answer_conflicts: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    deduplicated_images: int = 0


class ImportCommitService:
    def __init__(self, question_repository: QuestionRepository, asset_manager: AssetManager) -> None:
        self.question_repository = question_repository
        self.asset_manager = asset_manager

    def commit(
        self,
        candidates: list[ImportCandidate],
        *,
        actor_id: str | None,
        allow_exact_duplicates: bool = False,
        update_by_number: bool = False,
    ) -> CommitResult:
        result = CommitResult()
        if update_by_number:
            if actor_id is None:
                raise PermissionError("按编号更新需要管理员身份")
            administrator = AdminRepository(self.question_repository.database).require_active(actor_id)
            if administrator.role is not AdminRole.SUPERVISOR:
                raise PermissionError("按编号更新需要主管理员权限")
        for candidate in candidates:
            try:
                if (
                    not update_by_number
                    and candidate.question.display_number
                    and self.question_repository.find_by_display_number(candidate.question.display_number)
                ):
                    raise ValueError("编号已存在，请改用按编号更新模式")
                draft, deduplicated_images = self._materialize_assets(candidate)
                if update_by_number:
                    self._update_existing(candidate, draft, actor_id, result)
                    result.deduplicated_images += deduplicated_images
                    continue
                result.deduplicated_images += deduplicated_images
                duplicates = self.question_repository.find_exact_duplicates(draft)
                if duplicates and not allow_exact_duplicates:
                    result.skipped_exact_duplicates.append(candidate.source_location)
                    continue
                if self.question_repository.find_surface_conflicts(draft):
                    draft.status = QuestionStatus.DRAFT
                    if "答案冲突待复核" not in draft.tags:
                        draft.tags.append("答案冲突待复核")
                    result.answer_conflicts.append(candidate.source_location)
                result.saved.append(self.question_repository.create(draft, actor_id))
            except Exception as exc:
                result.errors.append((candidate.source_location, str(exc)))
        return result

    def _update_existing(self, candidate, draft, actor_id: str, result: CommitResult) -> None:
        matches = self.question_repository.find_by_display_number(draft.display_number)
        if not draft.display_number:
            raise ValueError("按编号更新时编号不能为空")
        if len(matches) != 1:
            raise ValueError(
                f"编号{draft.display_number}匹配到{len(matches)}道题，无法安全更新"
            )
        question_id, version = matches[0]
        original = self.question_repository.get(question_id)
        merged = _merge_for_update(original, draft, candidate.provided_fields)
        saved = self.question_repository.update(merged, actor_id, expected_version=version)
        changed_fields = _changed_fields(original, merged)
        with self.question_repository.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, actor_id, action, entity_type, entity_id, details_json, created_at
                ) VALUES (?, ?, 'excel_update_question', 'question', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    actor_id,
                    question_id,
                    json.dumps(
                        {
                            "display_number": merged.display_number,
                            "old_version": version,
                            "new_version": saved.version,
                            "changed_fields": changed_fields,
                            "source_location": candidate.source_location,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
        result.saved.append(saved)

    def _materialize_assets(self, candidate: ImportCandidate):
        draft = copy.deepcopy(candidate.question)
        assets_by_owner: dict[str, list[str]] = {}
        deduplicated_images = 0
        for pending in candidate.images:
            record = self.asset_manager.ingest_bytes(pending.data, pending.filename_hint)
            owner_assets = assets_by_owner.setdefault(pending.owner_key, [])
            if record.id in owner_assets:
                deduplicated_images += 1
            else:
                owner_assets.append(record.id)
        draft.question_asset_ids = assets_by_owner.get("stem", [])
        draft.options = [
            QuestionOption(
                option.key,
                option.text,
                tuple(assets_by_owner.get(option.key, [])),
            )
            for option in draft.options
        ]
        return draft, deduplicated_images


def _merge_for_update(original, incoming, provided_fields):
    if not provided_fields:
        return incoming
    metadata = {
        "basis": incoming.basis if "依据" in provided_fields else original.basis,
        "display_number": incoming.display_number,
        "status": incoming.status if "状态" in provided_fields else original.status,
        "usage_scope": incoming.usage_scope if "使用范围" in provided_fields else original.usage_scope,
        "applicable_year": incoming.applicable_year if "适用年份" in provided_fields else original.applicable_year,
        "source": incoming.source if "来源" in provided_fields else original.source,
        "chapter": incoming.chapter if "章节" in provided_fields else original.chapter,
        "clause": incoming.clause if "条款" in provided_fields else original.clause,
        "difficulty": incoming.difficulty if "难度" in provided_fields else original.difficulty,
        "tags": incoming.tags if "标签" in provided_fields else original.tags,
    }
    if not incoming.question_asset_ids and original.question_asset_ids:
        question_assets = list(original.question_asset_ids)
    else:
        question_assets = list(incoming.question_asset_ids)
    original_options = {option.key: option for option in original.options}
    options = []
    for option in incoming.options:
        asset_ids = option.asset_ids
        if not asset_ids and option.key in original_options:
            asset_ids = original_options[option.key].asset_ids
        options.append(QuestionOption(option.key, option.text, asset_ids))
    return copy.deepcopy(incoming).__class__(
        **metadata,
        question_type=incoming.question_type,
        stem=incoming.stem,
        options=options,
        correct_option_keys=set(incoming.correct_option_keys),
        blanks=copy.deepcopy(incoming.blanks),
        unordered_groups=copy.deepcopy(incoming.unordered_groups),
        question_asset_ids=question_assets,
        score=incoming.score,
        id=original.id,
    )


def _changed_fields(original, updated) -> list[str]:
    fields = (
        "question_type",
        "stem",
        "basis",
        "display_number",
        "status",
        "usage_scope",
        "applicable_year",
        "source",
        "chapter",
        "clause",
        "difficulty",
        "tags",
        "options",
        "correct_option_keys",
        "blanks",
        "unordered_groups",
        "question_asset_ids",
        "score",
    )
    return [field for field in fields if getattr(original, field) != getattr(updated, field)]
