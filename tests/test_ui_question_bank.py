from decimal import Decimal
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.security.passwords import hash_secret
from examdesk.ui.question_bank import QuestionBankPage


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_question_bank_page_filters_selects_and_batch_updates(
    tmp_path: Path,
    qt_application: QApplication,
    monkeypatch,
) -> None:
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    database_path = tmp_path / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    admins = AdminRepository(database)
    supervisor = admins.create_first_admin("主管理员", "supervisor-pass", hash_secret("RECOVERY").encode())
    repository = QuestionRepository(database)
    question = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="用于筛选的题目",
        basis="依据",
        display_number="001",
        status=QuestionStatus.ENABLED,
        chapter="第一章",
        tags=["分类", "重点内容"],
        options=[QuestionOption("A", "正确"), QuestionOption("B", "错误")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )
    repository.create(question, supervisor.id)
    page = QuestionBankPage(repository, AssetManager(database, tmp_path / "assets"), supervisor.id)
    page.clear_filters()
    page.show()
    qt_application.processEvents()

    assert page.table.rowCount() == 1
    assert page.table.horizontalHeaderItem(8).text() == "标签"
    assert page.table.item(0, 8).text() == "分类；重点内容"
    assert page.table.item(0, 8).toolTip() == "分类；重点内容"
    page.select_all_filtered()
    assert page.selected_ids == {question.id}
    page.batch_status(QuestionStatus.DISABLED)
    assert repository.get(question.id).status is QuestionStatus.DISABLED
    page.close()


def test_question_bank_page_applies_type_and_status_filters(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    database_path = tmp_path / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    admins = AdminRepository(database)
    supervisor = admins.create_first_admin("主管理员", "supervisor-pass", hash_secret("RECOVERY").encode())
    repository = QuestionRepository(database)
    for number, question_type, status in (
        ("001", QuestionType.SINGLE, QuestionStatus.ENABLED),
        ("002", QuestionType.MULTIPLE, QuestionStatus.ENABLED),
        ("003", QuestionType.SINGLE, QuestionStatus.DISABLED),
    ):
        repository.create(
            QuestionDraft(
                question_type=question_type,
                stem=f"筛选题目 {number}",
                basis="依据",
                display_number=number,
                status=status,
                options=[QuestionOption("A", "正确"), QuestionOption("B", "错误")],
                correct_option_keys={"A", "B"} if question_type is QuestionType.MULTIPLE else {"A"},
                score=Decimal("1"),
            ),
            supervisor.id,
        )
    page = QuestionBankPage(repository, AssetManager(database, tmp_path / "assets"), supervisor.id)
    page.type_combo.setCurrentIndex(page.type_combo.findData(QuestionType.SINGLE.value))
    page.status_combo.setCurrentIndex(page.status_combo.findData(QuestionStatus.ENABLED.value))

    page.apply_filters()

    assert [item.display_number for item in page.page_items] == ["001"]
    assert page.table.rowCount() == 1
    page.close()


def test_question_bank_page_sorts_all_filtered_rows_by_clicked_header(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    database_path = tmp_path / "sorted.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    admins = AdminRepository(database)
    supervisor = admins.create_first_admin(
        "主管理员", "supervisor-pass", hash_secret("RECOVERY").encode()
    )
    repository = QuestionRepository(database)
    for number, tags, score in (
        ("001", ["C类"], Decimal("10")),
        ("002", ["A类"], Decimal("2")),
        ("003", ["B类"], Decimal("1")),
    ):
        repository.create(
            QuestionDraft(
                question_type=QuestionType.SINGLE,
                stem=f"排序题 {number}",
                basis="",
                display_number=number,
                status=QuestionStatus.ENABLED,
                tags=tags,
                options=[QuestionOption("A", "正确"), QuestionOption("B", "错误")],
                correct_option_keys={"A"},
                score=score,
            ),
            supervisor.id,
        )
    page = QuestionBankPage(repository, AssetManager(database, tmp_path / "assets"), supervisor.id)

    page._sort_by_column(8)
    assert [item.display_number for item in page.page_items] == ["002", "003", "001"]
    page._sort_by_column(10)
    assert [item.display_number for item in page.page_items] == ["003", "002", "001"]
    page._sort_by_column(10)
    assert [item.display_number for item in page.page_items] == ["001", "002", "003"]
    page.close()


def test_question_bank_number_column_uses_natural_sorting(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    database_path = tmp_path / "natural-sorted.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    admins = AdminRepository(database)
    supervisor = admins.create_first_admin(
        "natural-sort-admin", "supervisor-pass", hash_secret("RECOVERY").encode()
    )
    repository = QuestionRepository(database)
    for number in ("1", "10", "11", "12", "13", "2", "3", "4", "5", "6", "7", "8", "9"):
        repository.create(
            QuestionDraft(
                question_type=QuestionType.SINGLE,
                stem=f"Natural sort {number}",
                basis="",
                display_number=number,
                status=QuestionStatus.ENABLED,
                options=[QuestionOption("A", "Correct"), QuestionOption("B", "Wrong")],
                correct_option_keys={"A"},
                score=Decimal("1"),
            ),
            supervisor.id,
        )
    page = QuestionBankPage(repository, AssetManager(database, tmp_path / "assets"), supervisor.id)

    page.sort_column = 1
    page.sort_order = Qt.SortOrder.AscendingOrder
    page.refresh()
    assert [item.display_number for item in page.page_items] == [str(value) for value in range(1, 14)]

    page._sort_by_column(1)
    assert [item.display_number for item in page.page_items] == [
        str(value) for value in range(13, 0, -1)
    ]
    page.close()
