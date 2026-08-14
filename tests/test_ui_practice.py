from PySide6.QtWidgets import QApplication, QLabel
from test_practice import make_question

from examdesk.domain.enums import QuestionType
from examdesk.practice import PracticeDefinition, PracticeSession
from examdesk.ui.practice_runner import PracticeRunnerPage


class _PracticeServiceStub:
    def save_progress(self, definition, grade) -> None:
        pass


def _page() -> PracticeRunnerPage:
    first = make_question("single-1", QuestionType.SINGLE)
    second = make_question("single-2", QuestionType.SINGLE)
    definition = PracticeDefinition("bank", "package", "练习", 1, (first, second), {})
    return PracticeRunnerPage(
        PracticeSession(definition, [first, second]),
        _PracticeServiceStub(),
    )


def test_practice_check_locks_answer_and_restores_feedback() -> None:
    application = QApplication.instance() or QApplication([])
    page = _page()
    question = page.session.questions[0]

    page.current_view.controls["B"].click()
    application.processEvents()
    page.check_current()
    application.processEvents()

    assert page.session.responses[question.id] == ["B"]
    assert question.id in page.checked_grades
    assert not page.current_view.controls["B"].isEnabled()
    assert page.number_buttons[0].toolTip() == "回答错误"
    assert any(
        label.text() == "正确答案：甲"
        for label in page.findChildren(QLabel)
    )

    page.jump_to(1)
    page.jump_to(0)
    application.processEvents()
    assert not page.current_view.controls["B"].isEnabled()
    assert page.check_button.text() == "已核对"


def test_practice_empty_answer_is_not_locked(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    page = _page()
    messages = []
    monkeypatch.setattr(
        "examdesk.ui.practice_runner.QMessageBox.information",
        lambda *args: messages.append(args[2]),
    )

    page.check_current()

    assert messages == ["请先完成本题。"]
    assert page.checked_grades == {}
    assert page.check_button.isEnabled()
