from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from test_reporting import prepare_report_database

from examdesk.domain.enums import AdminRole
from examdesk.time_display import format_local_datetime
from examdesk.ui.result_management import ResultManagementPage


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_attempt_table_uses_local_time_and_local_date_filter(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    database, _definition, _attempt_id = prepare_report_database(tmp_path)
    submitted = datetime(2026, 8, 4, 9, 0, tzinfo=UTC) + timedelta(minutes=20)
    page = ResultManagementPage(
        database,
        object(),
        "admin-test",
        AdminRole.SUPERVISOR,
    )
    page.date_filter.setText(submitted.astimezone().strftime("%Y-%m-%d"))
    page.refresh_attempts()
    qt_application.processEvents()

    assert page.table.rowCount() == 1
    assert page.table.item(0, 5).text() == format_local_datetime(submitted)

    page.date_filter.setText("2026-8-4")
    page.refresh_attempts()
    assert page.table.rowCount() == 0
    assert page.summary_label.text() == "日期格式错误，请输入 YYYY-MM-DD"
    page.close()
