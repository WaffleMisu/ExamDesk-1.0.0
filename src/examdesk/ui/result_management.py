from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from examdesk.domain.enums import AdminRole
from examdesk.maintenance import DataManagementService, SafetyBackupService
from examdesk.results import (
    ResultImportService,
    ResultReportService,
    ReviewService,
)
from examdesk.time_display import format_local_datetime, local_date_utc_bounds

from .admin_confirm import ReauthReasonDialog
from .table_sorting import (
    begin_table_update,
    configure_sorting,
    end_table_update,
    selected_identity,
    sortable_item,
)
from .window_sizing import fit_window_to_available


class SimilarReviewDialog(QDialog):
    def __init__(self, review_service: ReviewService, session_id: str, reviewer_id: str, parent=None) -> None:
        super().__init__(parent)
        self.review_service = review_service
        self.session_id = session_id
        self.reviewer_id = reviewer_id
        self.items = review_service.list_pending_similar_answers(session_id)
        self.setWindowTitle("相似填空复核")
        fit_window_to_available(self, 920, 560, minimum_width=660, minimum_height=420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        title = QLabel("相似填空复核")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ("考生", "题目编号", "空号", "考生答案", "标准答案", "相似度")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        configure_sorting(
            self.table,
            "similar_review",
            default_column=0,
            default_order=Qt.SortOrder.AscendingOrder,
        )
        sort_state = begin_table_update(self.table)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            values = (
                item.candidate_name,
                item.question_id,
                str(item.blank_index),
                item.response,
                item.accepted_answer,
                f"{item.similarity:.1f}%",
            )
            identity = f"{item.attempt_id}\x1f{item.question_id}\x1f{item.blank_index}"
            sort_values = (
                item.candidate_name.casefold(),
                item.question_id,
                item.blank_index,
                item.response,
                item.accepted_answer,
                item.similarity,
            )
            for column, (value, sort_value) in enumerate(zip(values, sort_values, strict=True)):
                self.table.setItem(
                    row,
                    column,
                    sortable_item(
                        value,
                        sort_value=sort_value,
                        identity=identity if column == 0 else None,
                    ),
                )
        end_table_update(self.table, sort_state)
        if self.items:
            self.table.selectRow(0)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        reject = QPushButton("维持判错")
        reject.clicked.connect(lambda: self._decide(False))
        buttons.addButton(reject, QDialogButtonBox.ButtonRole.ActionRole)
        accept = QPushButton("判对")
        accept.setObjectName("primaryButton")
        accept.clicked.connect(lambda: self._decide(True))
        buttons.addButton(accept, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _decide(self, accept: bool) -> None:
        row = self.table.currentRow()
        identity = selected_identity(self.table, row)
        if identity is None:
            return
        attempt_id, question_id, blank_index = identity.split("\x1f", 2)
        item = next(
            (
                candidate
                for candidate in self.items
                if candidate.attempt_id == attempt_id
                and candidate.question_id == question_id
                and candidate.blank_index == int(blank_index)
            ),
            None,
        )
        if item is None:
            return
        try:
            self.review_service.review_similar_answer(
                attempt_id=item.attempt_id,
                question_id=item.question_id,
                blank_index=item.blank_index,
                accept=accept,
                reviewer_id=self.reviewer_id,
            )
        except (KeyError, ValueError) as exc:
            QMessageBox.critical(self, "复核失败", str(exc))
            return
        self.items.remove(item)
        self.table.removeRow(row)
        if self.items:
            self.table.selectRow(min(row, len(self.items) - 1))


class ResultManagementPage(QWidget):
    def __init__(
        self,
        database,
        key_store,
        administrator_id: str,
        administrator_role: AdminRole | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self.key_store = key_store
        self.administrator_id = administrator_id
        self.administrator_role = administrator_role or _role_for(database, administrator_id)
        self.supervisor = self.administrator_role is AdminRole.SUPERVISOR
        self.management_service = DataManagementService(database)
        self.review_service = ReviewService(database)
        self.report_service = ResultReportService(database)
        self.session_ids: list[str] = []
        self.attempt_ids: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 30)
        layout.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel("答题记录")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        import_button = QPushButton("批量收卷")
        import_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        import_button.clicked.connect(self.import_results)
        header.addWidget(import_button)
        self.delete_button = QPushButton("删除所选答卷")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self.delete_selected)
        self.delete_button.setVisible(self.supervisor)
        header.addWidget(self.delete_button)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        self.session_combo = QComboBox()
        self.session_combo.currentIndexChanged.connect(self.refresh_attempts)
        toolbar.addWidget(self.session_combo, 1)
        review_button = QPushButton("相似填空复核")
        review_button.clicked.connect(self.review_similar)
        toolbar.addWidget(review_button)
        excel_button = QPushButton("导出Excel汇总")
        excel_button.clicked.connect(self.export_excel)
        toolbar.addWidget(excel_button)
        pdf_button = QPushButton("导出个人PDF")
        pdf_button.clicked.connect(self.export_pdf)
        toolbar.addWidget(pdf_button)
        layout.addLayout(toolbar)

        filters = QHBoxLayout()
        self.name_filter = QLineEdit()
        self.name_filter.setPlaceholderText("筛选考生姓名")
        self.name_filter.setClearButtonEnabled(True)
        self.name_filter.returnPressed.connect(self.refresh_attempts)
        filters.addWidget(self.name_filter, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部状态", None)
        self.status_filter.addItem("有效", "valid")
        self.status_filter.addItem("已作废", "void")
        filters.addWidget(self.status_filter)
        self.focus_filter = QComboBox()
        self.focus_filter.addItem("切屏不限", None)
        self.focus_filter.addItem("有切屏", "yes")
        self.focus_filter.addItem("无切屏", "no")
        filters.addWidget(self.focus_filter)
        self.review_filter = QComboBox()
        self.review_filter.addItem("复核不限", None)
        self.review_filter.addItem("有待复核", "yes")
        self.review_filter.addItem("无待复核", "no")
        filters.addWidget(self.review_filter)
        self.date_filter = QLineEdit()
        self.date_filter.setPlaceholderText("日期 YYYY-MM-DD")
        self.date_filter.setFixedWidth(150)
        filters.addWidget(self.date_filter)
        apply_filter = QPushButton("筛选")
        apply_filter.clicked.connect(self.refresh_attempts)
        filters.addWidget(apply_filter)
        layout.addLayout(filters)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("pageMeta")
        layout.addWidget(self.summary_label)
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ("姓名", "当前得分", "预估最高", "最终得分", "满分", "交卷时间", "切屏次数", "状态")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        configure_sorting(
            self.table,
            "attempts",
            default_column=5,
            default_order=Qt.SortOrder.DescendingOrder,
        )
        layout.addWidget(self.table, 1)
        self.refresh_sessions()

    def refresh_sessions(self) -> None:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name FROM sessions ORDER BY created_at DESC"
            ).fetchall()
        self.session_ids = [row["id"] for row in rows]
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for row in rows:
            self.session_combo.addItem(row["name"], row["id"])
        self.session_combo.blockSignals(False)
        self.refresh_attempts()

    def refresh_attempts(self) -> None:
        session_id = self._session_id()
        if session_id is None:
            self.table.setRowCount(0)
            self.attempt_ids = []
            self.summary_label.setText("暂无考试场次")
            return
        clauses = ["a.session_id = ?", "a.status IN ('submitted', 'void')"]
        parameters = [session_id]
        if self.name_filter.text().strip():
            clauses.append("a.candidate_name LIKE ?")
            parameters.append("%" + self.name_filter.text().strip() + "%")
        status = self.status_filter.currentData()
        if status == "valid":
            clauses.append("a.is_void = 0")
        elif status == "void":
            clauses.append("a.is_void = 1")
        if self.date_filter.text().strip():
            try:
                date_start, date_end = local_date_utc_bounds(self.date_filter.text())
            except ValueError as exc:
                self.table.setRowCount(0)
                self.attempt_ids = []
                self.summary_label.setText(str(exc))
                return
            clauses.append("COALESCE(a.submitted_at, a.started_at) >= ?")
            clauses.append("COALESCE(a.submitted_at, a.started_at) < ?")
            parameters.extend((date_start, date_end))
        review = self.review_filter.currentData()
        if review:
            operator = "EXISTS" if review == "yes" else "NOT EXISTS"
            clauses.append(
                f"{operator} (SELECT 1 FROM attempt_answers aa "
                "WHERE aa.attempt_id = a.id AND aa.similar_flags_json != '[]')"
            )
        having = ""
        if self.focus_filter.currentData() == "yes":
            having = " HAVING COUNT(fe.id) > 0"
        elif self.focus_filter.currentData() == "no":
            having = " HAVING COUNT(fe.id) = 0"
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, COUNT(fe.id) AS focus_count
                FROM attempts a
                LEFT JOIN foreground_events fe ON fe.attempt_id = a.id
                WHERE """
                + " AND ".join(clauses)
                + " GROUP BY a.id"
                + having
                + " ORDER BY a.submitted_at DESC",
                parameters,
            ).fetchall()
        self.attempt_ids = [row["id"] for row in rows]
        sort_state = begin_table_update(self.table)
        self.table.setRowCount(len(rows))
        for table_row, row in enumerate(rows):
            values = (
                row["candidate_name"],
                row["strict_score"] or "-",
                row["estimated_score"] or "-",
                row["final_score"] or row["strict_score"] or "-",
                row["max_score"],
                format_local_datetime(row["submitted_at"], empty="-"),
                str(row["focus_count"]),
                "已作废" if row["is_void"] else "有效",
            )
            def number(value):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")

            sort_values = (
                row["candidate_name"].casefold(),
                number(row["strict_score"]),
                number(row["estimated_score"]),
                number(row["final_score"] or row["strict_score"]),
                number(row["max_score"]),
                row["submitted_at"] or "",
                int(row["focus_count"]),
                int(row["is_void"]),
            )
            for column, (value, sort_value) in enumerate(zip(values, sort_values, strict=True)):
                self.table.setItem(
                    table_row,
                    column,
                    sortable_item(
                        str(value),
                        sort_value=sort_value,
                        identity=row["id"] if column == 0 else None,
                    ),
                )
        end_table_update(self.table, sort_state)
        if rows:
            self.table.selectRow(0)
        valid_count = sum(not row["is_void"] for row in rows)
        self.summary_label.setText(f"已导入 {len(rows)} 份 · 有效 {valid_count} 份")

    def import_results(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择答题记录文件夹",
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation),
        )
        if not folder:
            return
        try:
            importer = ResultImportService(
                self.database,
                self.key_store.load().result_recipient,
            )
            result = importer.import_folder(Path(folder), imported_by=self.administrator_id)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "收卷失败", str(exc))
            return
        self.refresh_sessions()
        duplicates = sum(item.duplicate_file for item in result.items)
        errors = result.error_count
        QMessageBox.information(
            self,
            "收卷完成",
            f"成功导入 {result.imported_count} 份，重复 {duplicates} 份，错误 {errors} 份。",
        )

    def review_similar(self) -> None:
        session_id = self._session_id()
        if session_id is None:
            return
        SimilarReviewDialog(
            self.review_service,
            session_id,
            self.administrator_id,
            self,
        ).exec()
        self.refresh_attempts()

    def export_excel(self) -> None:
        session_id = self._session_id()
        if session_id is None:
            return
        session_name = self.session_combo.currentText()
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出成绩汇总",
            str(Path(desktop) / f"{session_name}_成绩汇总.xlsx"),
            "Excel工作簿 (*.xlsx)",
        )
        if not path_text:
            return
        try:
            self.report_service.export_excel(session_id, Path(path_text).with_suffix(".xlsx"))
        except (OSError, KeyError, ValueError) as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", "成绩汇总已生成。")

    def export_pdf(self) -> None:
        row = self.table.currentRow()
        attempt_id = selected_identity(self.table, row)
        if attempt_id is None:
            QMessageBox.information(self, "选择答卷", "请先选择一名考生。")
            return
        candidate = self.table.item(row, 0).text()
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出个人答卷",
            str(Path(desktop) / f"{candidate}_答题记录.pdf"),
            "PDF文件 (*.pdf)",
        )
        if not path_text:
            return
        try:
            self.report_service.export_candidate_pdf(
                attempt_id,
                Path(path_text).with_suffix(".pdf"),
            )
        except (OSError, KeyError, ValueError) as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", "个人答卷PDF已生成。")

    def delete_selected(self) -> None:
        if not self.supervisor:
            return
        selected_rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        ids = [
            attempt_id
            for row in selected_rows
            if (attempt_id := selected_identity(self.table, row)) is not None
        ]
        if not ids:
            QMessageBox.information(self, "选择答卷", "请先选择一份或多份答卷。")
            return
        impact = self.management_service.attempt_delete_impact(ids)
        dialog = ReauthReasonDialog(
            "确认删除答题记录",
            f"将删除 {impact.attempts} 份答卷、{impact.answers} 条答案、"
            f"{impact.reviews} 条复核记录、{impact.foreground_events} 条切屏记录和"
            f" {impact.package_imports} 条收卷指纹。",
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup = SafetyBackupService(
                self.database,
                self.database.path.parent / "assets",
                self.key_store,
            )
            deleted = self.management_service.delete_attempts(
                ids,
                actor_id=self.administrator_id,
                password=dialog.password,
                reason=dialog.reason,
                backup=lambda: backup.create(password=dialog.password, operation="delete_attempts"),
            )
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.refresh_attempts()
        QMessageBox.information(self, "删除完成", f"已删除 {deleted.attempts} 份答题记录。")

    def _session_id(self) -> str | None:
        index = self.session_combo.currentIndex()
        return self.session_ids[index] if 0 <= index < len(self.session_ids) else None


def _role_for(database, actor_id: str) -> AdminRole | None:
    try:
        from examdesk.db import AdminRepository

        return AdminRepository(database).get(actor_id).role
    except KeyError:
        return None
