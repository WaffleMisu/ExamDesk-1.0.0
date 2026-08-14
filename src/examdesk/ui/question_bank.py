from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from examdesk.domain.enums import AdminRole, QuestionStatus, QuestionType, UsageScope
from examdesk.importers import (
    ImportCommitService,
    ImportPreview,
    parse_excel_xlsx,
    parse_legacy_txt_preview,
    parse_word_docx,
)
from examdesk.maintenance import DataManagementService, SafetyBackupService
from examdesk.questions import (
    AssetManager,
    ExcelExportError,
    QuestionExcelExporter,
    QuestionQuery,
    QuestionRepository,
)

from .admin_confirm import ReauthReasonDialog
from .practice_export import ExportPracticeDialog
from .question_editor import QuestionEditorDialog
from .window_sizing import fit_window_to_available

QUESTION_TYPE_TEXT = {
    QuestionType.SINGLE: "单选",
    QuestionType.MULTIPLE: "多选",
    QuestionType.JUDGE: "判断",
    QuestionType.FILL: "填空",
}
STATUS_TEXT = {
    QuestionStatus.DRAFT: "草稿",
    QuestionStatus.ENABLED: "启用",
    QuestionStatus.DISABLED: "停用",
}
SCOPE_TEXT = {
    UsageScope.BOTH: "考试和练习",
    UsageScope.EXAM_ONLY: "仅考试",
    UsageScope.PRACTICE_ONLY: "仅练习",
}


class ImportPreviewDialog(QDialog):
    def __init__(
        self,
        preview: ImportPreview,
        commit_service: ImportCommitService,
        actor_id: str,
        allow_update: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.preview = preview
        self.commit_service = commit_service
        self.actor_id = actor_id
        self.allow_update = allow_update
        self.imported_count = 0
        self.setWindowTitle("导入预览")
        fit_window_to_available(self, 1040, 680, minimum_width=700, minimum_height=440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        title_row = QHBoxLayout()
        title = QLabel("导入预览")
        title.setObjectName("dialogTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        summary = QLabel(
            f"{len(preview.candidates)} 道题 · {preview.error_count} 个错误 · {preview.warning_count} 个提醒"
        )
        summary.setObjectName("pageMeta")
        title_row.addWidget(summary)
        layout.addLayout(title_row)

        self.mode_combo = None
        if allow_update:
            mode_row = QHBoxLayout()
            mode_row.addWidget(QLabel("导入方式"))
            self.mode_combo = QComboBox()
            self.mode_combo.addItem("仅新增（默认）", False)
            self.mode_combo.addItem("按编号更新（主管理员）", True)
            mode_row.addWidget(self.mode_combo)
            mode_row.addStretch(1)
            layout.addLayout(mode_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._question_table())
        splitter.addWidget(self._issue_table())
        splitter.setSizes([430, 170])
        layout.addWidget(splitter, 1)

        self.blocking_label = QLabel()
        self.blocking_label.setObjectName("errorText")
        if preview.error_count:
            self.blocking_label.setText("存在错误，修正源文件后才能导入。")
        layout.addWidget(self.blocking_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_button = QPushButton(f"导入 {len(preview.candidates)} 道题")
        self.import_button.setObjectName("primaryButton")
        self.import_button.setEnabled(bool(preview.candidates) and preview.error_count == 0)
        self.import_button.clicked.connect(self._commit)
        buttons.addButton(self.import_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _question_table(self) -> QTableWidget:
        table = _make_table(("来源", "编号", "题型", "题目", "图片"))
        table.setRowCount(len(self.preview.candidates))
        for row, candidate in enumerate(self.preview.candidates):
            question = candidate.question
            values = (
                candidate.source_location,
                question.display_number,
                QUESTION_TYPE_TEXT[question.question_type],
                question.stem.replace("\n", " "),
                str(len(candidate.images)),
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        return table

    def _issue_table(self) -> QTableWidget:
        table = _make_table(("位置", "级别", "字段", "内容"))
        table.setRowCount(len(self.preview.issues))
        for row, issue in enumerate(self.preview.issues):
            values = (
                str(issue.row or "-"),
                "错误" if issue.severity == "error" else "提醒",
                issue.field or "-",
                issue.message,
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        return table

    def _commit(self) -> None:
        try:
            result = self.commit_service.commit(
                self.preview.candidates,
                actor_id=self.actor_id,
                update_by_number=bool(self.mode_combo and self.mode_combo.currentData()),
            )
        except (PermissionError, ValueError) as exc:
            self.blocking_label.setText(str(exc))
            return
        if result.errors:
            self.blocking_label.setText(
                "写入失败：" + "；".join(f"{location}：{message}" for location, message in result.errors[:3])
            )
            return
        self.imported_count = len(result.saved)
        skipped = len(result.skipped_exact_duplicates)
        message = f"成功导入或更新 {self.imported_count} 道题"
        if skipped:
            message += f"，跳过 {skipped} 道完全重复题"
        conflicts = len(result.answer_conflicts)
        if conflicts:
            message += f"，{conflicts} 道答案冲突题已导入为草稿并标记待复核"
        if result.deduplicated_images:
            message += f"，去除 {result.deduplicated_images} 张同位置重复图片"
        QMessageBox.information(self, "导入完成", message)
        self.accept()


class BatchMetadataDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.changes = {}
        self.tags_mode = "replace"
        self.setWindowTitle("批量修改题目信息")
        self.setFixedWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.addWidget(QLabel("勾选要修改的字段；未勾选字段保持不变。"))
        form = QFormLayout()
        self.controls = {}
        self.checks = {}
        self._add_combo(
            form,
            "状态",
            "status",
            [("草稿", QuestionStatus.DRAFT), ("启用", QuestionStatus.ENABLED), ("停用", QuestionStatus.DISABLED)],
        )
        self._add_combo(
            form,
            "使用范围",
            "usage_scope",
            [("考试和练习", UsageScope.BOTH), ("仅考试", UsageScope.EXAM_ONLY), ("仅练习", UsageScope.PRACTICE_ONLY)],
        )
        year = QSpinBox()
        year.setRange(0, 9999)
        year.setSpecialValueText("清空")
        self._add_control(form, "适用年份", "applicable_year", year)
        metadata_fields = (
            ("来源", "source"),
            ("章节", "chapter"),
            ("条款", "clause"),
            ("难度", "difficulty"),
            ("标签", "tags"),
        )
        for label, key in metadata_fields:
            self._add_control(form, label, key, QLineEdit())
        self.tags_mode_combo = QComboBox()
        self.tags_mode_combo.addItem("替换标签", "replace")
        self.tags_mode_combo.addItem("追加标签", "append")
        self.tags_mode_combo.addItem("移除标签", "remove")
        form.addRow("标签处理", self.tags_mode_combo)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        apply_button = QPushButton("应用修改")
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(self._accept)
        buttons.addButton(apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_combo(self, form, label, key, values) -> None:
        combo = QComboBox()
        for text, value in values:
            combo.addItem(text, value)
        self._add_control(form, label, key, combo)

    def _add_control(self, form, label, key, control) -> None:
        check = QCheckBox("修改")
        check.toggled.connect(control.setEnabled)
        control.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(check)
        row.addWidget(control, 1)
        form.addRow(label, row)
        self.checks[key] = check
        self.controls[key] = control

    def _accept(self) -> None:
        changes = {}
        for key, check in self.checks.items():
            if not check.isChecked():
                continue
            control = self.controls[key]
            if isinstance(control, QComboBox):
                changes[key] = control.currentData()
            elif isinstance(control, QSpinBox):
                changes[key] = control.value() or None
            else:
                if key == "tags":
                    changes[key] = [
                        part.strip()
                        for part in control.text().replace(",", ";").split(";")
                        if part.strip()
                    ]
                else:
                    changes[key] = control.text().strip()
        if not changes:
            self.error_label.setText("至少勾选一个修改字段")
            return
        self.changes = changes
        self.tags_mode = self.tags_mode_combo.currentData()
        self.accept()


class QuestionBankPage(QWidget):
    def __init__(
        self,
        repository: QuestionRepository,
        asset_manager: AssetManager,
        actor_id: str,
        practice_service=None,
        key_store=None,
        administrator_role: AdminRole | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.asset_manager = asset_manager
        self.actor_id = actor_id
        self.practice_service = practice_service
        self.key_store = key_store
        self.administrator_role = administrator_role or _role_for(repository, actor_id)
        self.supervisor = self.administrator_role is AdminRole.SUPERVISOR
        self.commit_service = ImportCommitService(repository, asset_manager)
        self.management_service = DataManagementService(repository.database)
        self.page_number = 1
        self.page_size = 100
        self.page_items = []
        self.selected_ids: set[str] = set()
        self.loading = False
        self.settings = QSettings("WaffleMisu", "ExamDesk")
        self.sort_columns = {
            1: "display_number",
            2: "question_type",
            3: "stem",
            4: "status",
            5: "usage_scope",
            6: "applicable_year",
            7: "chapter",
            8: "tags",
            9: "difficulty",
            10: "score",
        }
        self.sort_column = int(self.settings.value("bank/sort_column", 1))
        if self.sort_column not in self.sort_columns:
            self.sort_column = 1
        self.sort_order = Qt.SortOrder(
            int(self.settings.value("bank/sort_order", Qt.SortOrder.AscendingOrder.value))
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 30)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("题库")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        for text, slot, primary in (
            ("新建题目", self.create_question, True),
            ("编辑题目", self.edit_selected_question, False),
            ("批量导入", self.import_questions, False),
            ("导出 Excel", self.export_excel, False),
        ):
            button = QPushButton(text)
            if primary:
                button.setObjectName("primaryButton")
            button.clicked.connect(slot)
            header.addWidget(button)
        if practice_service is not None and key_store is not None:
            practice_button = QPushButton("导出练习包")
            practice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
            practice_button.clicked.connect(self.export_practice)
            header.addWidget(practice_button)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索编号、题目、依据或来源")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self.apply_filters)
        filters.addWidget(self.search_edit, 2)
        self.type_combo = _filter_combo("题型")
        type_values = (
            ("单选", QuestionType.SINGLE),
            ("多选", QuestionType.MULTIPLE),
            ("判断", QuestionType.JUDGE),
            ("填空", QuestionType.FILL),
        )
        for text, value in type_values:
            self.type_combo.addItem(text, value)
        filters.addWidget(self.type_combo)
        self.status_combo = _filter_combo("状态")
        status_values = (
            ("草稿", QuestionStatus.DRAFT),
            ("启用", QuestionStatus.ENABLED),
            ("停用", QuestionStatus.DISABLED),
        )
        for text, value in status_values:
            self.status_combo.addItem(text, value)
        filters.addWidget(self.status_combo)
        self.scope_combo = _filter_combo("使用范围")
        scope_values = (
            ("考试和练习", UsageScope.BOTH),
            ("仅考试", UsageScope.EXAM_ONLY),
            ("仅练习", UsageScope.PRACTICE_ONLY),
        )
        for text, value in scope_values:
            self.scope_combo.addItem(text, value)
        filters.addWidget(self.scope_combo)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 9999)
        self.year_spin.setSpecialValueText("年度不限")
        filters.addWidget(self.year_spin)
        apply_button = QPushButton("筛选")
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(self.apply_filters)
        filters.addWidget(apply_button)
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(self.clear_filters)
        filters.addWidget(clear_button)
        layout.addLayout(filters)

        advanced = QHBoxLayout()
        self.chapter_edit = _filter_edit("章节")
        self.clause_edit = _filter_edit("条款")
        self.source_edit = _filter_edit("来源")
        self.difficulty_edit = _filter_edit("难度")
        self.tags_edit = _filter_edit("标签，多个用分号")
        for edit in (self.chapter_edit, self.clause_edit, self.source_edit, self.difficulty_edit, self.tags_edit):
            advanced.addWidget(edit, 1)
        self.duplicate_check = QCheckBox("仅看疑似重复")
        advanced.addWidget(self.duplicate_check)
        layout.addLayout(advanced)

        action_row = QHBoxLayout()
        self.select_all_button = QPushButton("选择全部筛选结果")
        self.select_all_button.clicked.connect(self.select_all_filtered)
        action_row.addWidget(self.select_all_button)
        self.selection_label = QLabel("未选择题目")
        self.selection_label.setObjectName("pageMeta")
        action_row.addWidget(self.selection_label)
        action_row.addStretch(1)
        self.batch_button = QPushButton("批量修改")
        self.batch_button.clicked.connect(self.batch_edit)
        action_row.addWidget(self.batch_button)
        self.enable_button = QPushButton("批量启用")
        self.enable_button.clicked.connect(lambda: self.batch_status(QuestionStatus.ENABLED))
        action_row.addWidget(self.enable_button)
        self.disable_button = QPushButton("批量停用")
        self.disable_button.clicked.connect(lambda: self.batch_status(QuestionStatus.DISABLED))
        action_row.addWidget(self.disable_button)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self.delete_selected)
        self.delete_button.setVisible(self.supervisor)
        action_row.addWidget(self.delete_button)
        layout.addLayout(action_row)

        self.table = _make_table(
            ("选择", "编号", "题型", "题目", "状态", "范围", "年度", "章节", "标签", "难度", "分值")
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(self.sort_column, self.sort_order)
        self.table.horizontalHeader().sectionClicked.connect(self._sort_by_column)
        self.table.setColumnWidth(0, 54)
        self.table.setColumnWidth(1, 72)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(4, 70)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(7, 120)
        self.table.setColumnWidth(8, 160)
        self.table.setColumnWidth(9, 90)
        self.table.setColumnWidth(10, 70)
        self.table.itemChanged.connect(self._item_changed)
        self.table.doubleClicked.connect(self.edit_selected_question)
        layout.addWidget(self.table, 1)

        pager = QHBoxLayout()
        self.page_label = QLabel()
        self.page_label.setObjectName("pageMeta")
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        self.page_size_combo = QComboBox()
        for size in (50, 100, 200):
            self.page_size_combo.addItem(f"每页 {size}", size)
        self.page_size_combo.setCurrentIndex(1)
        self.page_size_combo.currentIndexChanged.connect(self._page_size_changed)
        pager.addWidget(self.page_size_combo)
        self.previous_button = QPushButton("上一页")
        self.previous_button.clicked.connect(self.previous_page)
        pager.addWidget(self.previous_button)
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self.next_page)
        pager.addWidget(self.next_button)
        layout.addLayout(pager)

        self._restore_filters()
        self.refresh()

    def refresh(self, *, clear_selection: bool = False) -> None:
        if clear_selection:
            self.selected_ids.clear()
        self.loading = True
        page = self.repository.list_filtered(self._query(), page=self.page_number, page_size=self.page_size)
        self.page_items = list(page.items)
        self.table.setRowCount(len(self.page_items))
        for row, item in enumerate(self.page_items):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(checkbox.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checkbox.setCheckState(Qt.CheckState.Checked if item.id in self.selected_ids else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, checkbox)
            values = (
                item.display_number,
                QUESTION_TYPE_TEXT[item.question_type],
                item.stem.replace("\n", " "),
                STATUS_TEXT[item.status],
                SCOPE_TEXT[item.usage_scope],
                str(item.applicable_year or ""),
                item.chapter,
                "；".join(item.tags),
                item.difficulty,
                str(item.score),
            )
            for column, value in enumerate(values, start=1):
                table_item = QTableWidgetItem(value)
                if column == 8:
                    table_item.setToolTip(value)
                self.table.setItem(row, column, table_item)
        self.loading = False
        total_pages = max(1, (page.total + page.page_size - 1) // page.page_size)
        self.count_label = getattr(self, "count_label", None)
        self.page_label.setText(f"筛选后 {page.total} 道 · 第 {page.page} / {total_pages} 页")
        self.previous_button.setEnabled(page.page > 1)
        self.next_button.setEnabled(page.page < total_pages)
        self._update_actions()

    def apply_filters(self) -> None:
        self._save_filters()
        self.page_number = 1
        self.refresh(clear_selection=True)

    def clear_filters(self) -> None:
        self.search_edit.clear()
        for combo in (self.type_combo, self.status_combo, self.scope_combo):
            combo.setCurrentIndex(0)
        self.year_spin.setValue(0)
        for edit in (self.chapter_edit, self.clause_edit, self.source_edit, self.difficulty_edit, self.tags_edit):
            edit.clear()
        self.duplicate_check.setChecked(False)
        self.apply_filters()

    def select_all_filtered(self) -> None:
        ids = self.repository.filtered_ids(self._query())
        self.selected_ids = set(ids)
        self.refresh()

    def previous_page(self) -> None:
        if self.page_number > 1:
            self.page_number -= 1
            self.refresh()

    def next_page(self) -> None:
        total = self.repository.list_filtered(self._query(), page=1, page_size=self.page_size).total
        if self.page_number * self.page_size < total:
            self.page_number += 1
            self.refresh()

    def _page_size_changed(self) -> None:
        self.page_size = int(self.page_size_combo.currentData())
        self.page_number = 1
        self.refresh()

    def _sort_by_column(self, column: int) -> None:
        if column not in self.sort_columns:
            return
        if column == self.sort_column:
            self.sort_order = (
                Qt.SortOrder.DescendingOrder
                if self.sort_order is Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self.sort_column = column
            self.sort_order = Qt.SortOrder.AscendingOrder
        self.settings.setValue("bank/sort_column", self.sort_column)
        self.settings.setValue("bank/sort_order", self.sort_order.value)
        self.table.horizontalHeader().setSortIndicator(self.sort_column, self.sort_order)
        self.page_number = 1
        self.refresh()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self.loading or item.column() != 0 or item.row() >= len(self.page_items):
            return
        question_id = self.page_items[item.row()].id
        if item.checkState() is Qt.CheckState.Checked:
            self.selected_ids.add(question_id)
        else:
            self.selected_ids.discard(question_id)
        self._update_actions()

    def _update_actions(self) -> None:
        count = len(self.selected_ids)
        self.selection_label.setText(f"已选择 {count} 道题" if count else "未选择题目")
        for button in (self.batch_button, self.enable_button, self.disable_button):
            button.setEnabled(count > 0)
        self.delete_button.setEnabled(count > 0)

    def _query(self) -> QuestionQuery:
        tags = tuple(part.strip() for part in self.tags_edit.text().replace(",", ";").split(";") if part.strip())
        question_type = self.type_combo.currentData()
        status = self.status_combo.currentData()
        usage_scope = self.scope_combo.currentData()
        return QuestionQuery(
            keyword=self.search_edit.text(),
            question_type=QuestionType(question_type) if question_type else None,
            status=QuestionStatus(status) if status else None,
            usage_scope=UsageScope(usage_scope) if usage_scope else None,
            applicable_year=self.year_spin.value() or None,
            chapter=self.chapter_edit.text(),
            clause=self.clause_edit.text(),
            source=self.source_edit.text(),
            difficulty=self.difficulty_edit.text(),
            tags=tags,
            duplicate_only=self.duplicate_check.isChecked(),
            sort_by=self.sort_columns[self.sort_column],
            sort_descending=self.sort_order is Qt.SortOrder.DescendingOrder,
        )

    def import_questions(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择题库文件",
            str(Path.home() / "Desktop"),
            "支持的题库 (*.xlsx *.docx *.txt);;Excel题库 (*.xlsx);;Word题库 (*.docx);;旧TXT题库 (*.txt)",
        )
        if not path_text:
            return
        path = Path(path_text)
        try:
            preview = _parse_import_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "无法读取题库", str(exc))
            return
        dialog = ImportPreviewDialog(
            preview,
            self.commit_service,
            self.actor_id,
            allow_update=self.supervisor and path.suffix.lower() == ".xlsx",
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.imported_count:
            self.refresh(clear_selection=True)

    def create_question(self) -> None:
        dialog = QuestionEditorDialog(self.repository, self.asset_manager, self.actor_id, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_question is not None:
            self.refresh(clear_selection=True)

    def edit_selected_question(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.page_items):
            QMessageBox.information(self, "选择题目", "请先选择一道题目。")
            return
        item = self.page_items[row]
        dialog = QuestionEditorDialog(
            self.repository,
            self.asset_manager,
            self.actor_id,
            question=self.repository.get(item.id),
            version=item.version,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_question is not None:
            self.refresh()

    def batch_status(self, status: QuestionStatus) -> None:
        try:
            changed = self.management_service.batch_update_questions(
                self.selected_ids,
                actor_id=self.actor_id,
                changes={"status": status},
            )
        except (PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "批量修改失败", str(exc))
            return
        self.selected_ids.clear()
        self.refresh()
        QMessageBox.information(self, "批量修改完成", f"已修改 {changed} 道题。")

    def batch_edit(self) -> None:
        dialog = BatchMetadataDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            changed = self.management_service.batch_update_questions(
                self.selected_ids,
                actor_id=self.actor_id,
                changes=dialog.changes,
                tags_mode=dialog.tags_mode,
            )
        except (PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "批量修改失败", str(exc))
            return
        self.selected_ids.clear()
        self.refresh()
        QMessageBox.information(self, "批量修改完成", f"已修改 {changed} 道题。")

    def delete_selected(self) -> None:
        if not self.supervisor or not self.selected_ids:
            return
        ids = tuple(self.selected_ids)
        with self.repository.database.connect() as connection:
            marks = ",".join("?" for _ in ids)
            referenced = connection.execute(
                f"SELECT COUNT(DISTINCT question_id) FROM session_questions WHERE question_id IN ({marks})",
                ids,
            ).fetchone()[0]
        impact = f"将处理 {len(ids)} 道题，其中 {referenced} 道有历史场次引用并会改为停用，其余可彻底删除。"
        dialog = ReauthReasonDialog("确认删除题目", impact, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup = SafetyBackupService(self.repository.database, self.asset_manager.root, self.key_store)
            result = self.management_service.delete_questions(
                ids,
                actor_id=self.actor_id,
                password=dialog.password,
                reason=dialog.reason,
                backup=lambda: backup.create(password=dialog.password, operation="delete_questions"),
                asset_root=self.asset_manager.root,
            )
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.selected_ids.clear()
        self.refresh()
        QMessageBox.information(
            self,
            "题库处理完成",
            f"彻底删除 {len(result.deleted_ids)} 道，因历史引用改为停用 {len(result.disabled_ids)} 道。",
        )

    def export_practice(self) -> None:
        ExportPracticeDialog(self.practice_service, self.key_store, self).exec()

    def export_excel(self) -> None:
        default_name = "题库导出_{}.xlsx".format(datetime.now().strftime("%Y%m%d_%H%M"))
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出题库 Excel",
            str(Path.home() / "Desktop" / default_name),
            "Excel 题库 (*.xlsx)",
        )
        if not path_text:
            return
        path = Path(path_text).with_suffix(".xlsx")
        try:
            ids = self.selected_ids or None
            result = QuestionExcelExporter(self.repository, self.asset_manager).export(path, ids)
        except (ExcelExportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        message = f"已导出 {result.question_count} 道题、{result.image_count} 张图片。\n{result.path}"
        QMessageBox.information(self, "导出完成", message)

    def _restore_filters(self) -> None:
        self.search_edit.setText(self.settings.value("bank/keyword", "", str))
        self.chapter_edit.setText(self.settings.value("bank/chapter", "", str))
        self.clause_edit.setText(self.settings.value("bank/clause", "", str))
        self.source_edit.setText(self.settings.value("bank/source", "", str))
        self.difficulty_edit.setText(self.settings.value("bank/difficulty", "", str))
        self.tags_edit.setText(self.settings.value("bank/tags", "", str))

    def _save_filters(self) -> None:
        for key, edit in (
            ("keyword", self.search_edit),
            ("chapter", self.chapter_edit),
            ("clause", self.clause_edit),
            ("source", self.source_edit),
            ("difficulty", self.difficulty_edit),
            ("tags", self.tags_edit),
        ):
            self.settings.setValue(f"bank/{key}", edit.text())


def _filter_combo(placeholder: str) -> QComboBox:
    combo = QComboBox()
    combo.addItem(placeholder, None)
    return combo


def _filter_edit(placeholder: str) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    return edit


def _role_for(repository: QuestionRepository, actor_id: str) -> AdminRole | None:
    try:
        from examdesk.db import AdminRepository

        return AdminRepository(repository.database).get(actor_id).role
    except (KeyError, ValueError):
        return None


def _parse_import_file(path: Path) -> ImportPreview:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return parse_excel_xlsx(path)
    if suffix == ".docx":
        return parse_word_docx(path)
    if suffix == ".txt":
        return parse_legacy_txt_preview(path)
    raise ValueError("只支持XLSX、DOCX和TXT题库")


def _make_table(headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(False)
    table.setFrameShape(QFrame.Shape.NoFrame)
    return table
