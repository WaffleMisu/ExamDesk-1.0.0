from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from examdesk.domain.enums import QuestionType
from examdesk.scoring import BlankGrade, SimilarityLevel, grade_choice, grade_fill
from examdesk.sessions import ExamDefinition

from .state import ExamState


@dataclass(frozen=True, slots=True)
class QuestionGrade:
    question_id: str
    strict_score: Decimal
    estimated_score: Decimal
    max_score: Decimal
    is_correct: bool
    is_partial: bool
    blank_grades: tuple[BlankGrade, ...] = ()


@dataclass(frozen=True, slots=True)
class ExamGrade:
    strict_score: Decimal
    estimated_score: Decimal
    max_score: Decimal
    questions: tuple[QuestionGrade, ...]


class ExamGrader:
    @staticmethod
    def grade(
        definition: ExamDefinition,
        state: ExamState,
        similarity_level: SimilarityLevel | None = None,
        custom_similarity_threshold: float | None = None,
    ) -> ExamGrade:
        if similarity_level is None:
            similarity_level = definition.similarity_level
            custom_similarity_threshold = definition.custom_similarity_threshold
        by_id = {item.question_id: item.question for item in definition.questions}
        results = []
        for displayed in state.displayed_questions:
            question = by_id[displayed.question_id]
            response = state.responses.get(displayed.question_id)
            if question.question_type is QuestionType.FILL:
                responses = list(response) if isinstance(response, (list, tuple)) else []
                fill_grade = grade_fill(
                    responses,
                    question.blanks,
                    question.unordered_groups,
                    similarity_level,
                    custom_similarity_threshold,
                )
                results.append(
                    QuestionGrade(
                        question_id=question.id,
                        strict_score=fill_grade.strict_score,
                        estimated_score=fill_grade.estimated_score,
                        max_score=fill_grade.max_score,
                        is_correct=fill_grade.strict_score == fill_grade.max_score,
                        is_partial=Decimal("0") < fill_grade.strict_score < fill_grade.max_score,
                        blank_grades=fill_grade.blanks,
                    )
                )
                continue
            selected = set(response) if isinstance(response, (list, tuple, set)) else set()
            choice_grade = grade_choice(
                question.question_type,
                selected,
                question.correct_option_keys,
                question.score,
            )
            results.append(
                QuestionGrade(
                    question_id=question.id,
                    strict_score=choice_grade.score,
                    estimated_score=choice_grade.score,
                    max_score=choice_grade.max_score,
                    is_correct=choice_grade.is_correct,
                    is_partial=choice_grade.is_partial,
                )
            )
        return ExamGrade(
            strict_score=sum((item.strict_score for item in results), Decimal("0")),
            estimated_score=sum((item.estimated_score for item in results), Decimal("0")),
            max_score=sum((item.max_score for item in results), Decimal("0")),
            questions=tuple(results),
        )
