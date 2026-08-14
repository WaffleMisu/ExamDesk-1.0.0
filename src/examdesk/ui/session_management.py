from __future__ import annotations

from datetime import UTC
from pathlib import Path

from PySide6.QtCore import QDateTime, QStandardPaths, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from examdesk.domain.enums import AdminRole, QuestionType, ReviewPolicy, SessionStatus
from examdesk.maintenance import DataManagementService, SafetyBackupService
from examdesk.sessions import RosterEntry, SessionFilter, SessionService
from examdesk.version import __version__

from .admin_confirm import ReauthReasonDialog
from .similarity_settings import SimilaritySettingsControl, similarity_label
from .table_sorting import (
    begin_table_update,
    configure_sorting,
    end_table_update,
    selected_identity,
    sortable_item,
)
from .window_sizing import fit_window_to_available

TYPE_LABELS = {
    QuestionType.SINGLE: "单选题",
    QuestionType.MULTIPLE: "多选题",
    QuestionType.JUDGE: "判断题",
    QuestionType.FILL: "填空题",
}
POLICY_VALUES = (
    ("立即显示答案与依据", ReviewPolicy.IMMEDIATE),
    ("指定时间后显示", ReviewPolicy.AFTER_RELEASE),
    ("只显示成绩", ReviewPolicy.SCORE_ONLY),
)
STATUS_LABELS = {
    SessionStatus.DRAFT: "草稿",
    SessionStatus.LOCKED: "已锁定",
    SessionStatus.ARCHIVED: "已归档",
}


class CreateSessionDialog(QDialog):
    def __init__(self, service: SessionService, actor_id: str, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.actor_id = actor_id
        self.created_session = None
        self.setWindowTitle("创建考试场次")
        fit_window_to_available(
            self,
            760,
            760,
            minimum_width=620,
            minimum_height=460,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(14)
        title = QLabel("创建考试场次")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("sessionFormScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.name_edit = QLineEdit()
        self.description_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("留空则考生无需密码")
        form.addRow("场次名称", self.name_edit)
        form.addRow("场次说明", self.description_edit)
        form.addRow("考试密码（可选）", self.password_edit)
        layout.addLayout(form)

        count_title = QLabel("固定题量")
        count_title.setObjectName("entryTitle")
        layout.addWidget(count_title)
        count_grid = QGridLayout()
        count_grid.setHorizontalSpacing(18)
        self.count_labels = {}
        self.count_spins = {}
        for index, question_type in enumerate(QuestionType):
            label = QLabel(f"{TYPE_LABELS[question_type]}（正在统计）")
            spin = QSpinBox()
            spin.setRange(0, 0)
            self.count_labels[question_type] = label
            self.count_spins[question_type] = spin
            count_grid.addWidget(label, index // 2, (index % 2) * 2)
            count_grid.addWidget(spin, index // 2, (index % 2) * 2 + 1)
        layout.addLayout(count_grid)

        settings = QFormLayout()
        settings.setHorizontalSpacing(18)
        settings.setVerticalSpacing(12)
        self.max_attempts = QSpinBox()
        self.max_attempts.setRange(1, 10)
        self.max_attempts.setValue(1)
        settings.addRow("最大答题次数", self.max_attempts)

        duration_row = QHBoxLayout()
        self.duration_enabled = QCheckBox("限时")
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 600)
        self.duration_spin.setValue(60)
        self.duration_spin.setSuffix(" 分钟")
        self.duration_spin.setEnabled(False)
        self.duration_enabled.toggled.connect(self.duration_spin.setEnabled)
        duration_row.addWidget(self.duration_enabled)
        duration_row.addWidget(self.duration_spin)
        duration_row.addStretch(1)
        settings.addRow("考试时长", duration_row)

        self.policy_combo = QComboBox()
        for label, policy in POLICY_VALUES:
            self.policy_combo.addItem(label, policy)
        self.policy_combo.currentIndexChanged.connect(self._policy_changed)
        settings.addRow("交卷后查看", self.policy_combo)
        self.release_edit = QDateTimeEdit()
        self.release_edit.setCalendarPopup(True)
        self.release_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.release_edit.setDateTime(QDateTime.currentDateTime().addDays(1))
        self.release_edit.setEnabled(False)
        settings.addRow("答案开放时间", self.release_edit)
        self.similarity_settings = SimilaritySettingsControl()
        settings.addRow("填空相似度", self.similarity_settings)
        self.monitoring_enabled = QCheckBox("启用切屏监控")
        self.monitoring_enabled.setToolTip("记录切出考试软件后的软件名称、进程名和窗口标题")
        settings.addRow("考试监控", self.monitoring_enabled)
        monitoring_hint = QLabel(
            "默认关闭。启用后，考生开考前必须确认监控告知。"
        )
        monitoring_hint.setObjectName("formHint")
        monitoring_hint.setWordWrap(True)
        settings.addRow("", monitoring_hint)
        layout.addLayout(settings)

        filters = QFormLayout()
        filters.setHorizontalSpacing(18)
        filters.setVerticalSpacing(12)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 2100)
        self.year_spin.setSpecialValueText("不限")
        self.year_spin.setValue(0)
        self.chapter_edit = QLineEdit()
        self.chapter_edit.setPlaceholderText("多个章节用英文分号隔开；留空表示不限")
        self.difficulty_edit = QLineEdit()
        self.difficulty_edit.setPlaceholderText("多个难度用英文分号隔开")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("多个标签用英文分号隔开")
        filters.addRow("适用年度", self.year_spin)
        filters.addRow("章节筛选", self.chapter_edit)
        filters.addRow("难度筛选", self.difficulty_edit)
        filters.addRow("标签筛选", self.tags_edit)
        layout.addLayout(filters)

        self.availability_error_label = QLabel()
        self.availability_error_label.setObjectName("errorText")
        self.availability_error_label.setWordWrap(True)
        layout.addWidget(self.availability_error_label)
        self.availability_timer = QTimer(self)
        self.availability_timer.setSingleShot(True)
        self.availability_timer.setInterval(180)
        self.availability_timer.timeout.connect(self._refresh_available_counts)
        self.year_spin.valueChanged.connect(self._schedule_available_counts)
        self.chapter_edit.textChanged.connect(self._schedule_available_counts)
        self.difficulty_edit.textChanged.connect(self._schedule_available_counts)
        self.tags_edit.textChanged.connect(self._schedule_available_counts)

        self.roster_enabled = QCheckBox("启用考生名单")
        layout.addWidget(self.roster_enabled)
        self.roster_edit = QTextEdit()
        self.roster_edit.setPlaceholderText("每行一个姓名，可在姓名后用制表符填写部门")
        self.roster_edit.setMaximumHeight(100)
        self.roster_edit.setEnabled(False)
        self.roster_enabled.toggled.connect(self.roster_edit.setEnabled)
        layout.addWidget(self.roster_edit)

        layout.addStretch(1)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        create = QPushButton("创建并抽题")
        create.setObjectName("primaryButton")
        create.clicked.connect(self._create)
        self.buttons.addButton(create, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._refresh_available_counts()

    def _policy_changed(self) -> None:
        self.release_edit.setEnabled(
            ReviewPolicy(self.policy_combo.currentData()) is ReviewPolicy.AFTER_RELEASE
        )

    def _session_filter(self) -> SessionFilter:
        return SessionFilter(
            applicable_year=self.year_spin.value() or None,
            chapters=frozenset(_split_values(self.chapter_edit.text())),
            difficulties=frozenset(_split_values(self.difficulty_edit.text())),
            tags=frozenset(_split_values(self.tags_edit.text())),
        )

    def _schedule_available_counts(self, *_args) -> None:
        self.availability_timer.start()

    def _refresh_available_counts(self) -> None:
        try:
            counts = self.service.available_question_counts(self._session_filter())
        except Exception as exc:
            for question_type in QuestionType:
                self.count_labels[question_type].setText(
                    f"{TYPE_LABELS[question_type]}（数量读取失败）"
                )
                self.count_spins[question_type].setMaximum(100)
            self.availability_error_label.setText(f"可用题量读取失败：{exc}")
            return
        self.availability_error_label.clear()
        for question_type in QuestionType:
            count = counts.get(question_type, 0)
            self.count_labels[question_type].setText(
                f"{TYPE_LABELS[question_type]}（可用 {count} 道）"
            )
            self.count_spins[question_type].setMaximum(min(100, count))

    def _create(self) -> None:
        policy = ReviewPolicy(self.policy_combo.currentData())
        release_at = None
        if policy is ReviewPolicy.AFTER_RELEASE:
            release_at = self.release_edit.dateTime().toPython()
            if release_at.tzinfo is None:
                release_at = release_at.astimezone()
            release_at = release_at.astimezone(UTC)
        try:
            self.created_session = self.service.create_draft(
                name=self.name_edit.text(),
                description=self.description_edit.text(),
                password=self.password_edit.text(),
                session_filter=self._session_filter(),
                question_counts={
                    question_type: spin.value()
                    for question_type, spin in self.count_spins.items()
                },
                max_attempts=self.max_attempts.value(),
                roster=_parse_roster(self.roster_edit.toPlainText()),
                roster_required=self.roster_enabled.isChecked(),
                duration_minutes=(self.duration_spin.value() if self.duration_enabled.isChecked() else None),
                review_policy=policy,
                review_release_at=release_at,
                min_software_version=__version__,
                created_by=self.actor_id,
                monitoring_enabled=self.monitoring_enabled.isChecked(),
                similarity_level=self.similarity_settings.level,
                custom_similarity_threshold=self.similarity_settings.custom_threshold,
            )
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()


class SessionManagementPage(QWidget):
    def __init__(
        self,
        service: SessionService,
        key_store,
        actor_id: str,
        administrator_role: AdminRole | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.key_store = key_store
        self.actor_id = actor_id
        self.administrator_role = administrator_role or _role_for(service.database, actor_id)
        self.supervisor = self.administrator_role is AdminRole.SUPERVISOR
        self.management_service = DataManagementService(service.database)
        self.session_ids: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 30)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("考试场次")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.export_button = QPushButton("锁定并导出")
        self.export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_button.clicked.connect(self.export_selected)
        header.addWidget(self.export_button)
        create = QPushButton("创建场次")
        create.setObjectName("primaryButton")
        create.clicked.connect(self.create_session)
        header.addWidget(create)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self.delete_selected)
        self.delete_button.setVisible(self.supervisor)
        header.addWidget(self.delete_button)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.name_filter = QLineEdit()
        self.name_filter.setPlaceholderText("筛选场次名称")
        self.name_filter.setClearButtonEnabled(True)
        self.name_filter.returnPressed.connect(self.refresh)
        filters.addWidget(self.name_filter, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部活动状态", None)
        self.status_filter.addItem("草稿", SessionStatus.DRAFT)
        self.status_filter.addItem("已锁定", SessionStatus.LOCKED)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status_filter)
        self.show_archived = QCheckBox("显示已归档")
        self.show_archived.toggled.connect(self.refresh)
        filters.addWidget(self.show_archived)
        layout.addLayout(filters)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ("场次名称", "状态", "题量", "总分", "时长", "相似度", "查看策略")
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        configure_sorting(self.table, "sessions", default_order=Qt.SortOrder.AscendingOrder)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        clauses = []
        parameters = []
        if self.name_filter.text().strip():
            clauses.append("name LIKE ?")
            parameters.append("%" + self.name_filter.text().strip() + "%")
        status = self.status_filter.currentData()
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        elif not self.show_archived.isChecked():
            clauses.append("status != 'archived'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.service.database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM sessions" + where + " ORDER BY created_at DESC",
                parameters,
            ).fetchall()
        sessions = [self.service.get(row["id"]) for row in rows]
        self.session_ids = [session.id for session in sessions]
        sort_state = begin_table_update(self.table)
        self.table.setRowCount(len(sessions))
        policy_text = {policy: label for label, policy in POLICY_VALUES}
        for row, session in enumerate(sessions):
            values = (
                session.name,
                STATUS_LABELS[session.status],
                str(len(session.questions)),
                str(session.max_score),
                f"{session.duration_minutes}分钟" if session.duration_minutes else "不限时",
                similarity_label(
                    session.similarity_level,
                    session.custom_similarity_threshold,
                ),
                policy_text[session.review_policy],
            )
            sort_values = (
                session.name.casefold(),
                STATUS_LABELS[session.status],
                len(session.questions),
                float(session.max_score),
                session.duration_minutes or float("inf"),
                values[5],
                values[6],
            )
            for column, (value, sort_value) in enumerate(zip(values, sort_values, strict=True)):
                self.table.setItem(
                    row,
                    column,
                    sortable_item(value, sort_value=sort_value, identity=session.id if column == 0 else None),
                )
        end_table_update(self.table, sort_state)
        if sessions:
            self.table.selectRow(0)
        self.delete_button.setEnabled(bool(sessions))

    def create_session(self) -> None:
        dialog = CreateSessionDialog(self.service, self.actor_id, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def export_selected(self) -> None:
        session_id = selected_identity(self.table, self.table.currentRow())
        if session_id is None:
            QMessageBox.information(self, "选择场次", "请先选择一个考试场次。")
            return
        session = self.service.get(session_id)
        if session.status is SessionStatus.ARCHIVED:
            QMessageBox.warning(self, "无法导出", "已归档场次不能导出。")
            return
        password = ""
        if self.service.password_required(session_id):
            password_dialog = PasswordConfirmDialog(session.name, self)
            if password_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            password = password_dialog.password
        try:
            if session.status is SessionStatus.DRAFT:
                self.service.lock(session_id)
            keys = self.key_store.load()
            package = self.service.export_package(
                session_id,
                password=password,
                signer=keys.signing,
                result_recipient=keys.result_recipient,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "无法导出场次", str(exc))
            return
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出正式考试包",
            str(Path(desktop) / f"{session.name}.exampack"),
            "正式考试包 (*.exampack)",
        )
        if not path_text:
            return
        path = Path(path_text)
        if path.suffix.lower() != ".exampack":
            path = path.with_suffix(".exampack")
        try:
            path.write_bytes(package)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "导出完成", f"考试包已保存：\n{path}")

    def delete_selected(self) -> None:
        if not self.supervisor:
            return
        session_id = selected_identity(self.table, self.table.currentRow())
        if session_id is None:
            QMessageBox.information(self, "选择场次", "请先选择一个考试场次。")
            return
        with self.service.database.connect() as connection:
            attempt_count = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        action = "归档并默认隐藏" if attempt_count else "彻底删除"
        dialog = ReauthReasonDialog(
            "确认删除考试场次",
            f"该场次有 {attempt_count} 份答题记录，将执行：{action}。",
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup = SafetyBackupService(
                self.service.database,
                self.service.assets.root,
                self.key_store,
            )
            result = self.management_service.delete_sessions(
                [session_id],
                actor_id=self.actor_id,
                password=dialog.password,
                reason=dialog.reason,
                backup=lambda: backup.create(password=dialog.password, operation="delete_session"),
            )
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "场次处理失败", str(exc))
            return
        self.refresh()
        message = "场次已彻底删除" if result.deleted_ids else "场次含有答题记录，已归档并隐藏"
        QMessageBox.information(self, "场次处理完成", message)


class PasswordConfirmDialog(QDialog):
    def __init__(self, session_name: str, parent=None) -> None:
        super().__init__(parent)
        self.password = ""
        self.setWindowTitle("确认考试密码")
        self.setFixedWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        title = QLabel(session_name)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText("输入创建场次时设置的密码")
        self.edit.returnPressed.connect(self._accept)
        layout.addWidget(self.edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.edit.text():
            return
        self.password = self.edit.text()
        self.accept()


def _split_values(value: str) -> list[str]:
    return [part.strip() for part in value.replace(",", ";").split(";") if part.strip()]


def _parse_roster(value: str) -> list[RosterEntry]:
    result = []
    for line in value.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("\t", 1)]
        result.append(RosterEntry(parts[0], parts[1] if len(parts) > 1 else ""))
    return result


def _role_for(database, actor_id: str) -> AdminRole | None:
    try:
        from examdesk.db import AdminRepository

        return AdminRepository(database).get(actor_id).role
    except KeyError:
        return None
