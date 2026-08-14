from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

SORT_VALUE_ROLE = Qt.ItemDataRole.UserRole
IDENTITY_ROLE = Qt.ItemDataRole.UserRole + 1


class SortableTableItem(QTableWidgetItem):
    def __lt__(self, other) -> bool:
        left = self.data(SORT_VALUE_ROLE)
        right = other.data(SORT_VALUE_ROLE)
        if left is None or right is None:
            return super().__lt__(other)
        try:
            return left < right
        except TypeError:
            return str(left) < str(right)


def sortable_item(text: str, *, sort_value=None, identity: str | None = None) -> SortableTableItem:
    item = SortableTableItem(text)
    item.setData(SORT_VALUE_ROLE, text.casefold() if sort_value is None else sort_value)
    if identity is not None:
        item.setData(IDENTITY_ROLE, identity)
    return item


def configure_sorting(
    table: QTableWidget,
    settings_key: str,
    *,
    default_column: int = 0,
    default_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
) -> None:
    settings = QSettings("WaffleMisu", "ExamDesk")
    column = int(settings.value(f"sorting/{settings_key}/column", default_column))
    if not 0 <= column < table.columnCount():
        column = default_column
    order = Qt.SortOrder(
        int(settings.value(f"sorting/{settings_key}/order", default_order.value))
    )
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    header.setSortIndicator(column, order)

    def remember(selected_column: int, selected_order: Qt.SortOrder) -> None:
        settings.setValue(f"sorting/{settings_key}/column", selected_column)
        settings.setValue(f"sorting/{settings_key}/order", selected_order.value)

    header.sortIndicatorChanged.connect(remember)
    table.setSortingEnabled(True)


def begin_table_update(table: QTableWidget) -> tuple[int, Qt.SortOrder]:
    header = table.horizontalHeader()
    state = (header.sortIndicatorSection(), header.sortIndicatorOrder())
    table.setSortingEnabled(False)
    return state


def end_table_update(table: QTableWidget, state: tuple[int, Qt.SortOrder]) -> None:
    column, order = state
    table.setSortingEnabled(True)
    table.sortItems(column, order)


def selected_identity(table: QTableWidget, row: int) -> str | None:
    item = table.item(row, 0)
    value = item.data(IDENTITY_ROLE) if item is not None else None
    return str(value) if value else None
