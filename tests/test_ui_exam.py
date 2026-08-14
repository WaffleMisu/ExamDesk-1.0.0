
import pytest
from PySide6.QtWidgets import QApplication, QLineEdit
from test_exam_runtime import exam_definition, make_state

from examdesk.ui.question_view import QuestionView


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_question_view_restores_choice_and_emits_internal_option_keys(
    qt_application: QApplication,
) -> None:
    definition = exam_definition()
    state = make_state(definition)
    displayed = next(item for item in state.displayed_questions if item.question_id == "single")
    question = next(item.question for item in definition.questions if item.question_id == "single")
    view = QuestionView(question, displayed.option_order, ["A"], {})
    responses = []
    view.response_changed.connect(responses.append)

    assert view.controls["A"].isChecked()
    view.controls["B"].click()
    qt_application.processEvents()
    assert responses[-1] == ["B"]


def test_question_view_collects_all_fill_inputs(qt_application: QApplication) -> None:
    definition = exam_definition()
    question = next(item.question for item in definition.questions if item.question_id == "fill")
    view = QuestionView(question, (), [], {})
    responses = []
    view.response_changed.connect(responses.append)

    edit = view.controls["1"]
    assert isinstance(edit, QLineEdit)
    edit.setText("永久基本农田区")
    qt_application.processEvents()
    assert responses[-1] == ["永久基本农田区"]
