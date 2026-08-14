from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import QuestionType
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.ui.question_editor import QuestionEditorDialog


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_question_editor_saves_structured_unordered_fill_question(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    database_path = tmp_path / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    repository = QuestionRepository(database)
    dialog = QuestionEditorDialog(
        repository,
        AssetManager(database, tmp_path / "assets"),
        "admin",
    )
    dialog.type_combo.setCurrentIndex(3)
    dialog.stem_edit.setPlainText("请填写（1）（2）（3）")
    dialog.basis_edit.setPlainText("培训手册")
    dialog.add_blank_row()
    dialog.add_blank_row()
    for row, answer in enumerate(("甲/第一", "乙/第二", "丙/第三")):
        dialog.blank_table.cellWidget(row, 1).setText(answer)
        dialog.blank_table.cellWidget(row, 2).setValue(0.5)
        dialog.blank_table.cellWidget(row, 3).setText("1")

    dialog.save()
    qt_application.processEvents()

    assert dialog.saved_question is not None
    saved = repository.get(dialog.saved_question.id)
    assert saved.question_type is QuestionType.FILL
    assert len(saved.blanks) == 3
    assert saved.unordered_groups[0].indexes == (1, 2, 3)
    assert str(saved.score) == "1.5"

    editor = QuestionEditorDialog(
        repository,
        AssetManager(database, tmp_path / "assets"),
        "admin",
        question=saved,
        version=dialog.saved_question.version,
    )
    editor.stem_edit.setPlainText("修改后的题目（1）（2）（3）")
    editor.save()
    qt_application.processEvents()

    assert editor.saved_question.version == 2
    assert repository.get(saved.id).stem.startswith("修改后的题目")
