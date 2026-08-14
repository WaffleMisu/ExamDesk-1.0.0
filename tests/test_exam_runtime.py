from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from examdesk.domain.enums import (
    MatchMode,
    QuestionStatus,
    QuestionType,
    ReviewPolicy,
    UsageScope,
)
from examdesk.domain.models import BlankDefinition, QuestionDraft, QuestionOption
from examdesk.exam import ExamGrader, ExamState, ExamStateStore, ExamStateStoreError, ExamTimer
from examdesk.scoring import SimilarityLevel
from examdesk.sessions import ExamDefinition, SessionQuestion


def choice_question(identifier: str, question_type: QuestionType, correct: set[str]) -> QuestionDraft:
    options = [QuestionOption(key, text) for key, text in zip("ABCD", "甲乙丙丁", strict=True)]
    if question_type is QuestionType.JUDGE:
        options = [QuestionOption("A", "正确"), QuestionOption("B", "错误")]
    return QuestionDraft(
        id=identifier,
        question_type=question_type,
        stem=identifier,
        basis="依据",
        status=QuestionStatus.ENABLED,
        usage_scope=UsageScope.BOTH,
        options=options,
        correct_option_keys=correct,
        score=Decimal("2"),
    )


def fill_question(identifier: str) -> QuestionDraft:
    return QuestionDraft(
        id=identifier,
        question_type=QuestionType.FILL,
        stem="（1）",
        basis="依据",
        status=QuestionStatus.ENABLED,
        usage_scope=UsageScope.BOTH,
        blanks=[
            BlankDefinition(
                1,
                ("永久基本农田区",),
                Decimal("2"),
                MatchMode.TEXT_SIMILARITY,
            )
        ],
        score=Decimal("2"),
    )


def exam_definition(duration_minutes: int | None = 30) -> ExamDefinition:
    questions = [
        choice_question("single", QuestionType.SINGLE, {"A"}),
        choice_question("multiple", QuestionType.MULTIPLE, {"A", "B", "C"}),
        choice_question("judge", QuestionType.JUDGE, {"B"}),
        fill_question("fill"),
    ]
    return ExamDefinition(
        session_id="session-1",
        package_id="package-1",
        name="测试",
        description="",
        max_attempts=1,
        duration_minutes=duration_minutes,
        review_policy=ReviewPolicy.IMMEDIATE,
        review_release_at=None,
        min_software_version="2.0.0",
        random_seed="fixed-seed",
        session_auth_key=b"s" * 32,
        result_recipient_public_key=b"r" * 32,
        questions=tuple(
            SessionQuestion(question.id, 1, index, question)
            for index, question in enumerate(questions, start=1)
        ),
        roster=(),
        roster_required=False,
        assets={},
    )


def make_state(definition: ExamDefinition | None = None) -> ExamState:
    return ExamState.create(
        definition or exam_definition(),
        candidate_name="测试用户甲",
        machine_name="PC-01",
        windows_user="test_user",
        software_version="2.0.0",
        now=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
        attempt_id="attempt-fixed",
    )


def test_exam_state_randomizes_reproducibly_and_keeps_judge_option_order() -> None:
    definition = exam_definition()
    first = make_state(definition)
    second = make_state(definition)

    assert first.displayed_questions == second.displayed_questions
    assert {item.question_id for item in first.displayed_questions} == {
        item.question_id for item in definition.questions
    }
    by_id = {item.question_id: item for item in first.displayed_questions}
    assert by_id["judge"].option_order == ("A", "B")
    assert set(by_id["single"].option_order) == {"A", "B", "C", "D"}


def test_exam_navigation_responses_marks_and_unanswered_status() -> None:
    state = make_state()
    first_id = state.displayed_questions[0].question_id
    state.set_response(first_id, ["A"])
    assert first_id not in state.unanswered_question_ids()
    assert state.toggle_mark(first_id)
    assert not state.toggle_mark(first_id)
    assert state.go_next() == 1
    assert state.go_previous() == 0
    assert state.jump_to(4) == 3
    with pytest.raises(IndexError):
        state.jump_to(5)


def test_encrypted_exam_state_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    definition = exam_definition()
    state = make_state(definition)
    state.set_response("single", ["A"])
    store = ExamStateStore([tmp_path / "primary", tmp_path / "backup"], b"k" * 32)
    store.save(state)

    restored = store.load(definition)
    assert restored.to_payload() == state.to_payload()

    path = store.paths[0]
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    store.paths[1].unlink()
    with pytest.raises(ExamStateStoreError, match="damaged"):
        store.load(definition)


def test_exam_state_files_can_be_isolated_by_package(tmp_path: Path) -> None:
    first = ExamStateStore(
        [tmp_path / "state"],
        b"k" * 32,
        filename="active_exam_first.state",
    )
    second = ExamStateStore(
        [tmp_path / "state"],
        b"k" * 32,
        filename="active_exam_second.state",
    )
    first.paths[0].parent.mkdir(parents=True, exist_ok=True)
    first.paths[0].write_bytes(b"broken old package state")

    assert first.paths[0].name != second.paths[0].name
    with pytest.raises(FileNotFoundError):
        second.load(object())


class FakeClock:
    def __init__(self, wall: datetime, monotonic_value: float = 0.0) -> None:
        self.wall = wall
        self.monotonic_value = monotonic_value

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: int, *, wall_seconds: int | None = None) -> None:
        self.monotonic_value += seconds
        self.wall += timedelta(seconds=seconds if wall_seconds is None else wall_seconds)


def test_timer_counts_monotonic_time_and_does_not_extend_on_clock_rollback() -> None:
    state = make_state()
    clock = FakeClock(state.started_at)
    timer = ExamTimer(state, clock)

    clock.advance(1200)
    assert timer.remaining_seconds() == 600
    assert timer.due_warnings() == [600]
    timer.checkpoint()

    clock.advance(300, wall_seconds=-3600)
    assert timer.remaining_seconds() == 300
    assert state.time_anomaly
    clock.advance(300, wall_seconds=0)
    assert timer.is_expired()


def test_exam_grader_combines_partial_multiple_and_similar_fill_scores() -> None:
    definition = exam_definition()
    state = make_state(definition)
    state.set_response("single", ["A"])
    state.set_response("multiple", ["A", "B"])
    state.set_response("judge", ["A"])
    state.set_response("fill", ["永久基本农用区"])

    grade = ExamGrader.grade(definition, state)

    assert grade.strict_score == Decimal("3")
    assert grade.estimated_score == Decimal("5")
    assert grade.max_score == Decimal("8")
    multiple = next(item for item in grade.questions if item.question_id == "multiple")
    assert multiple.is_partial


def test_exam_grader_uses_custom_similarity_threshold_from_package() -> None:
    definition = exam_definition()
    fill = next(item.question for item in definition.questions if item.question_id == "fill")
    fill.blanks = [
        BlankDefinition(1, ("我有一个梦想",), Decimal("2"), MatchMode.TEXT_SIMILARITY)
    ]
    definition = replace(
        definition,
        similarity_level=SimilarityLevel.CUSTOM,
        custom_similarity_threshold=66.0,
    )
    state = make_state(definition)
    state.set_response("fill", ["有梦想"])

    grade = ExamGrader.grade(definition, state)
    fill_grade = next(item for item in grade.questions if item.question_id == "fill")

    assert fill_grade.strict_score == Decimal("0")
    assert fill_grade.estimated_score == Decimal("2")
