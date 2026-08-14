from PySide6.QtWidgets import QApplication, QDialogButtonBox

from examdesk.domain.enums import QuestionType
from examdesk.ui.session_management import CreateSessionDialog


class AvailabilityService:
    def available_question_counts(self, session_filter):
        if "安全考试" in session_filter.tags:
            return {
                QuestionType.SINGLE: 8,
                QuestionType.MULTIPLE: 3,
                QuestionType.JUDGE: 2,
                QuestionType.FILL: 1,
            }
        return {
            QuestionType.SINGLE: 36,
            QuestionType.MULTIPLE: 12,
            QuestionType.JUDGE: 20,
            QuestionType.FILL: 6,
        }


def test_create_session_dialog_shows_and_refreshes_available_counts() -> None:
    application = QApplication.instance() or QApplication([])
    dialog = CreateSessionDialog(AvailabilityService(), "admin")

    assert dialog.count_labels[QuestionType.SINGLE].text() == "单选题（可用 36 道）"
    assert dialog.count_spins[QuestionType.SINGLE].maximum() == 36
    assert dialog.monitoring_enabled.isChecked() is False

    dialog.tags_edit.setText("安全考试")
    dialog.availability_timer.stop()
    dialog._refresh_available_counts()
    application.processEvents()

    assert dialog.count_labels[QuestionType.SINGLE].text() == "单选题（可用 8 道）"
    assert dialog.count_labels[QuestionType.FILL].text() == "填空题（可用 1 道）"
    assert dialog.count_spins[QuestionType.SINGLE].maximum() == 8
    assert dialog.count_spins[QuestionType.FILL].maximum() == 1
    dialog.close()


def test_create_session_dialog_keeps_actions_visible_at_small_size() -> None:
    application = QApplication.instance() or QApplication([])
    dialog = CreateSessionDialog(AvailabilityService(), "admin")
    dialog.resize(640, 480)
    dialog.show()
    application.processEvents()

    button_box = dialog.findChild(QDialogButtonBox)
    assert button_box is dialog.buttons
    assert button_box.geometry().bottom() <= dialog.contentsRect().bottom()
    assert dialog.scroll.verticalScrollBar().maximum() > 0

    dialog.close()
