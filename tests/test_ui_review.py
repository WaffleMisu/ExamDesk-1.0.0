from datetime import UTC, datetime
from decimal import Decimal

from PySide6.QtWidgets import QApplication, QLabel
from test_assets import make_image_bytes
from test_practice import make_question

from examdesk.domain.enums import QuestionType, ReviewPolicy
from examdesk.domain.models import QuestionOption
from examdesk.practice import PracticeDefinition, PracticeSession
from examdesk.questions import question_to_payload
from examdesk.results import SubmittedReview
from examdesk.ui.exam_result import ExamResultPage
from examdesk.ui.image_viewer import ImageStrip
from examdesk.ui.practice_runner import PracticeResultPage
from examdesk.ui.review_question import ReviewQuestionPanel


def _question_with_images():
    question = make_question("image-question", QuestionType.SINGLE)
    question.question_asset_ids = ["stem-image"]
    question.options = [
        QuestionOption("A", "甲", ("a-image",)),
        QuestionOption("B", "乙"),
        QuestionOption("C", "丙"),
        QuestionOption("D", "丁"),
    ]
    assets = {
        "stem-image": make_image_bytes((30, 120, 180), (60, 40)),
        "a-image": make_image_bytes((180, 70, 40), (60, 40)),
    }
    return question, assets


def test_practice_result_displays_question_and_option_images() -> None:
    QApplication.instance() or QApplication([])
    question, assets = _question_with_images()
    definition = PracticeDefinition("bank", "package", "练习", 1, (question,), assets)
    session = PracticeSession(definition, [question], {question.id: ["B"]})
    page = PracticeResultPage(session, session.grade())

    assert len(page.findChildren(ReviewQuestionPanel)) == 1
    assert len(page.findChildren(ImageStrip)) == 2


def test_exam_result_displays_images_and_respects_option_order() -> None:
    QApplication.instance() or QApplication([])
    question, assets = _question_with_images()
    review = SubmittedReview(
        attempt_id="attempt",
        candidate_name="测试用户甲",
        submitted_at=datetime(2026, 8, 6, tzinfo=UTC),
        strict_score=Decimal("0"),
        estimated_score=Decimal("0"),
        max_score=Decimal("2"),
        policy=ReviewPolicy.IMMEDIATE,
        release_at=None,
        details_visible=True,
        questions=(
            {
                "snapshot": question_to_payload(question),
                "response": ["B"],
                "strict_score": "0",
                "estimated_score": "0",
                "option_order": ["B", "A", "C", "D"],
            },
        ),
    )
    page = ExamResultPage(review, assets)

    assert len(page.findChildren(ReviewQuestionPanel)) == 1
    assert len(page.findChildren(ImageStrip)) == 2
    assert any(label.text() == "A. 乙（你的选择）" for label in page.findChildren(QLabel))
