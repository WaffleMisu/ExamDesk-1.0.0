from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget

from examdesk.ui.table_sorting import (
    begin_table_update,
    end_table_update,
    selected_identity,
    sortable_item,
)


def test_numeric_sorting_keeps_row_identity_with_visible_record() -> None:
    application = QApplication.instance() or QApplication([])
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(("姓名", "分数"))
    table.setSortingEnabled(True)
    begin_table_update(table)
    table.setRowCount(3)
    for row, (identity, name, score) in enumerate(
        (("id-10", "十分", 10), ("id-2", "二分", 2), ("id-1", "一分", 1))
    ):
        table.setItem(row, 0, sortable_item(name, identity=identity))
        table.setItem(row, 1, sortable_item(str(score), sort_value=score))
    end_table_update(table, (1, Qt.SortOrder.AscendingOrder))
    application.processEvents()

    assert [table.item(row, 1).text() for row in range(3)] == ["1", "2", "10"]
    assert [selected_identity(table, row) for row in range(3)] == ["id-1", "id-2", "id-10"]
    table.close()
