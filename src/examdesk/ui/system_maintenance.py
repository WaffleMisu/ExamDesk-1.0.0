from __future__ import annotations

import atexit
import json
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from examdesk.db import Administrator, AdminRepository
from examdesk.domain.enums import AdminRole
from examdesk.maintenance import (
    BackupService,
    FactoryResetService,
    OrphanAttemptService,
)
from examdesk.questions import AssetManager, BankCollaborationService, QuestionRepository
from examdesk.security import OrganizationKeyError, OrganizationKeyStore
from examdesk.time_display import format_local_datetime
from examdesk.version import __version__

from .admin_confirm import ReauthReasonDialog
from .collaboration_ui import IssueWorkPackageDialog
from .table_sorting import (
    begin_table_update,
    configure_sorting,
    end_table_update,
    selected_identity,
    sortable_item,
)
from .window_sizing import fit_window_to_available


class SystemMaintenancePage(QWidget):
    def __init__(
        self,
        key_store: OrganizationKeyStore,
        database,
        asset_root: Path,
        administrator: Administrator,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.key_store = key_store
        self.database = database
        self.asset_root = asset_root
        self.administrator = administrator
        self.admin_repository = AdminRepository(database)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 28, 34, 30)
        layout.setSpacing(18)
        title = QLabel("系统维护")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        trust_panel = QFrame()
        trust_panel.setObjectName("summaryPanel")
        panel_layout = QVBoxLayout(trust_panel)
        panel_layout.setContentsMargins(22, 20, 22, 20)
        panel_layout.setSpacing(12)
        panel_title = QLabel("考试包信任证书")
        panel_title.setObjectName("entryTitle")
        panel_layout.addWidget(panel_title)
        self.status_label = QLabel()
        self.status_label.setObjectName("pageMeta")
        panel_layout.addWidget(self.status_label)
        button_row = QHBoxLayout()
        export_button = QPushButton("导出信任证书")
        export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        export_button.clicked.connect(self.export_certificate)
        button_row.addWidget(export_button)
        import_button = QPushButton("导入信任证书")
        import_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        import_button.clicked.connect(self.import_certificate)
        button_row.addWidget(import_button)
        button_row.addStretch(1)
        panel_layout.addLayout(button_row)
        layout.addWidget(trust_panel)

        backup_panel = QFrame()
        backup_panel.setObjectName("summaryPanel")
        backup_layout = QVBoxLayout(backup_panel)
        backup_layout.setContentsMargins(22, 20, 22, 20)
        backup_title = QLabel("数据备份")
        backup_title.setObjectName("entryTitle")
        backup_layout.addWidget(backup_title)
        backup_hint = QLabel("题库、图片、管理员和答题记录将写入一个加密备份包。")
        backup_hint.setObjectName("pageMeta")
        backup_layout.addWidget(backup_hint)
        backup_buttons = QHBoxLayout()
        backup_button = QPushButton("创建加密备份")
        backup_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        backup_button.clicked.connect(self.create_backup)
        backup_buttons.addWidget(backup_button)
        if administrator.role is AdminRole.SUPERVISOR:
            restore_button = QPushButton("恢复加密备份")
            restore_button.setObjectName("restoreBackupButton")
            restore_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
            )
            restore_button.clicked.connect(self.restore_backup)
            backup_buttons.addWidget(restore_button)
        backup_buttons.addStretch(1)
        backup_layout.addLayout(backup_buttons)
        layout.addWidget(backup_panel)

        admin_panel = QFrame()
        admin_panel.setObjectName("summaryPanel")
        admin_layout = QVBoxLayout(admin_panel)
        admin_layout.setContentsMargins(22, 20, 22, 20)
        admin_title = QLabel("管理员账户")
        admin_title.setObjectName("entryTitle")
        admin_layout.addWidget(admin_title)
        self.admin_status = QLabel()
        self.admin_status.setObjectName("pageMeta")
        admin_layout.addWidget(self.admin_status)
        if administrator.role is AdminRole.SUPERVISOR:
            admin_buttons = QHBoxLayout()
            add_admin = QPushButton("添加副管理员")
            add_admin.clicked.connect(self.add_administrator)
            admin_buttons.addWidget(add_admin)
            remove_admin = QPushButton("移除副管理员")
            remove_admin.setObjectName("dangerButton")
            remove_admin.clicked.connect(self.remove_administrator)
            admin_buttons.addWidget(remove_admin)
            rotate_recovery = QPushButton("重新生成恢复码")
            rotate_recovery.setObjectName("rotateRecoveryCodeButton")
            rotate_recovery.clicked.connect(self.rotate_recovery_code)
            admin_buttons.addWidget(rotate_recovery)
            admin_buttons.addStretch(1)
            admin_layout.addLayout(admin_buttons)
        layout.addWidget(admin_panel)

        if administrator.role is AdminRole.SUPERVISOR:
            collaboration_panel = QFrame()
            collaboration_panel.setObjectName("summaryPanel")
            collaboration_layout = QVBoxLayout(collaboration_panel)
            collaboration_layout.setContentsMargins(22, 20, 22, 20)
            collaboration_title = QLabel("离线管理员协作")
            collaboration_title.setObjectName("entryTitle")
            collaboration_layout.addWidget(collaboration_title)
            collaboration_hint = QLabel(
                "向普通管理员签发题库工作包，并将其返回的题库变更包合并到权威题库。"
            )
            collaboration_hint.setObjectName("pageMeta")
            collaboration_hint.setWordWrap(True)
            collaboration_layout.addWidget(collaboration_hint)
            collaboration_buttons = QHBoxLayout()
            issue_button = QPushButton("签发协作工作包")
            issue_button.clicked.connect(self.issue_work_package)
            collaboration_buttons.addWidget(issue_button)
            import_patch_button = QPushButton("导入题库变更包")
            import_patch_button.clicked.connect(self.import_bank_patch)
            collaboration_buttons.addWidget(import_patch_button)
            collaboration_buttons.addStretch(1)
            collaboration_layout.addLayout(collaboration_buttons)
            layout.addWidget(collaboration_panel)

        governance_panel = QFrame()
        governance_panel.setObjectName("summaryPanel")
        governance_layout = QVBoxLayout(governance_panel)
        governance_layout.setContentsMargins(22, 20, 22, 20)
        governance_title = QLabel("数据治理")
        governance_title.setObjectName("entryTitle")
        governance_layout.addWidget(governance_title)
        self.orphan_status = QLabel()
        self.orphan_status.setObjectName("pageMeta")
        governance_layout.addWidget(self.orphan_status)
        governance_buttons = QHBoxLayout()
        orphan_button = QPushButton("处理无法恢复的考试")
        orphan_button.clicked.connect(self.manage_orphan_attempts)
        governance_buttons.addWidget(orphan_button)
        audit_button = QPushButton("查看操作审计")
        audit_button.clicked.connect(self.open_audit_log)
        governance_buttons.addWidget(audit_button)
        if administrator.role is AdminRole.SUPERVISOR:
            reset_button = QPushButton("恢复出厂状态")
            reset_button.setObjectName("dangerButton")
            reset_button.clicked.connect(self.factory_reset)
            governance_buttons.addWidget(reset_button)
        governance_buttons.addStretch(1)
        governance_layout.addLayout(governance_buttons)
        layout.addWidget(governance_panel)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        try:
            keys = self.key_store.ensure_initialized()
            trusted_count = len(self.key_store.trusted_public_keys())
            self.status_label.setText(f"组织签名编号：{keys.signing.id} · 本机可信证书：{trusted_count} 个")
        except (OSError, OrganizationKeyError) as exc:
            self.status_label.setText(str(exc))
        admins = self.admin_repository.list_all()
        active_count = sum(item.is_active for item in admins)
        self.admin_status.setText(
            " · ".join(
                f"{item.name}（{'主管理员' if item.role is AdminRole.SUPERVISOR else '副管理员'}"
                f"{'，已移除' if not item.is_active else ''}）"
                for item in admins
            )
            + f"    有效账号 {active_count} / 3 人"
        )
        orphan_count = len(self._orphan_service().list_unrecoverable())
        self.orphan_status.setText(
            f"检测到 {orphan_count} 场无法恢复的进行中考试" if orphan_count else "未发现无法恢复的考试"
        )

    def export_certificate(self) -> None:
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出信任证书",
            str(Path(desktop) / "ExamDesk 离线考试系统_考试信任证书.examtrust"),
            "考试信任证书 (*.examtrust)",
        )
        if not path_text:
            return
        path = Path(path_text)
        if path.suffix.lower() != ".examtrust":
            path = path.with_suffix(".examtrust")
        try:
            path.write_bytes(self.key_store.export_trust_certificate())
        except (OSError, OrganizationKeyError) as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"信任证书已保存：\n{path}")

    def import_certificate(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "导入信任证书",
            str(Path.home() / "Desktop"),
            "考试信任证书 (*.examtrust)",
        )
        if not path_text:
            return
        try:
            signer_id = self.key_store.import_trust_certificate(
                Path(path_text).read_bytes(),
                Path(path_text).name,
            )
        except (OSError, OrganizationKeyError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "导入完成", f"已信任签名编号：{signer_id}")

    def create_backup(self) -> None:
        password, accepted = QInputDialog.getText(
            self,
            "设置备份密码",
            "请输入备份密码：",
            QLineEdit.EchoMode.Password,
        )
        if not accepted or not password:
            return
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "保存加密备份",
            str(Path(desktop) / "ExamDesk 离线考试系统_备份.exambackup"),
            "加密备份 (*.exambackup)",
        )
        if not path_text:
            return
        path = Path(path_text)
        if path.suffix.lower() != ".exambackup":
            path = path.with_suffix(".exambackup")
        try:
            BackupService(self.database, self.asset_root).create(
                path,
                password=password,
                key_store=self.key_store,
                software_version=__version__,
            )
        except (OSError, OrganizationKeyError, ValueError) as exc:
            QMessageBox.critical(self, "备份失败", str(exc))
            return
        QMessageBox.information(self, "备份完成", f"加密备份已保存：\n{path}")

    def restore_backup(self) -> None:
        dialog = BackupRestoreDialog(
            self.database,
            self.asset_root,
            self.key_store,
            actor_id=self.administrator.id,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.restored is None:
            return
        QMessageBox.information(
            self,
            "恢复完成",
            f"已恢复 {dialog.restored.asset_count} 个图片资源。软件将退出，"
            "请重新打开后使用恢复的数据。",
        )
        QApplication.instance().quit()

    def add_administrator(self) -> None:
        dialog = AddAdministratorDialog(self.admin_repository, self.administrator.id, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def remove_administrator(self) -> None:
        ordinary = [
            item
            for item in self.admin_repository.list_all()
            if item.role is AdminRole.ADMIN and item.is_active
        ]
        if not ordinary:
            QMessageBox.information(self, "没有可移除账号", "当前没有有效的副管理员账号。")
            return
        labels = [item.name for item in ordinary]
        selected_name, accepted = QInputDialog.getItem(
            self,
            "移除副管理员",
            "选择需要移除的副管理员：",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        target = ordinary[labels.index(selected_name)]
        dialog = ReauthReasonDialog(
            "移除副管理员",
            f"移除 {target.name} 后，该账号不能再登录，已签发的旧协作授权立即作废；"
            "历史题目修改和操作审计记录会保留。",
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.admin_repository.deactivate_admin(
                self.administrator.id,
                target.id,
                supervisor_password=dialog.password,
                reason=dialog.reason,
            )
        except (KeyError, PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "移除失败", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "移除完成", f"副管理员 {target.name} 已移除。")

    def rotate_recovery_code(self) -> None:
        dialog = RotateRecoveryCodeDialog(
            self.admin_repository,
            self.administrator.id,
            self,
        )
        dialog.exec()

    def _collaboration_service(self) -> BankCollaborationService:
        return BankCollaborationService(
            self.database,
            QuestionRepository(self.database),
            AssetManager(self.database, self.asset_root),
            self.admin_repository,
        )

    def issue_work_package(self) -> None:
        ordinary = [
            item
            for item in self.admin_repository.list_all()
            if item.role is AdminRole.ADMIN and item.is_active
        ]
        if not ordinary:
            QMessageBox.information(self, "没有普通管理员", "请先在管理员账户区域添加普通管理员。")
            return
        dialog = IssueWorkPackageDialog(ordinary, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_admin is None:
            return
        desktop = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation))
        default_name = f"{dialog.selected_admin.name}_题库工作包.bankwork"
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "保存协作工作包",
            str(desktop / default_name),
            "协作工作包 (*.bankwork)",
        )
        if not path_text:
            return
        path = Path(path_text).with_suffix(".bankwork")
        try:
            keys = self.key_store.load()
            data = self._collaboration_service().issue_work_package(
                admin_id=dialog.selected_admin.id,
                package_password=dialog.package_password,
                signer=keys.signing,
                master_recipient=keys.result_recipient,
                minimum_software_version=__version__,
            )
            path.write_bytes(data)
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "签发失败", str(exc))
            return
        QMessageBox.information(
            self,
            "签发完成",
            f"已为 {dialog.selected_admin.name} 生成协作工作包：\n{path}\n\n重新签发后，旧工作授权已作废。",
        )

    def import_bank_patch(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择题库变更包",
            str(Path.home() / "Desktop"),
            "题库变更包 (*.bankpatch)",
        )
        if not path_text:
            return
        path = Path(path_text)
        try:
            result = self._collaboration_service().import_patch(
                path.read_bytes(),
                master_recipient=self.key_store.load().result_recipient,
                imported_by=self.administrator.id,
                source_path=str(path),
            )
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        if result.replayed:
            QMessageBox.information(self, "无需重复导入", "该题库变更包以前已经导入。")
            return
        details = [
            f"成功合并：{len(result.applied)} 道",
            f"冲突待处理：{len(result.conflicts)} 道",
            f"导入失败：{len(result.errors)} 道",
        ]
        for conflict in result.conflicts[:5]:
            details.append(f"冲突 {conflict.source_location}：{conflict.reason}")
        for location, message in result.errors[:5]:
            details.append(f"错误 {location}：{message}")
        QMessageBox.information(self, "变更包处理完成", "\n".join(details))

    def manage_orphan_attempts(self) -> None:
        OrphanAttemptsDialog(
            self._orphan_service(),
            self.administrator.id,
            self,
        ).exec()
        self.refresh()

    def open_audit_log(self) -> None:
        AuditLogDialog(self.database, self).exec()

    def factory_reset(self) -> None:
        service = FactoryResetService(self.database, self.database.path.parent)
        dialog = FactoryResetDialog(
            service,
            self.key_store,
            self.asset_root,
            self.administrator.id,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.staged_directory is None:
            return
        staged = dialog.staged_directory
        atexit.register(shutil.rmtree, staged, True)
        QMessageBox.information(
            self,
            "重置完成",
            "所有本机数据已清空，软件现在退出。再次启动时将进入首次初始化。",
        )
        QApplication.instance().quit()

    def _orphan_service(self) -> OrphanAttemptService:
        root = self.database.path.parent
        return OrphanAttemptService(self.database, [root / "state", root / "state_backup"])


class BackupRestoreDialog(QDialog):
    def __init__(
        self,
        database,
        asset_root: Path,
        key_store: OrganizationKeyStore,
        *,
        actor_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self.asset_root = asset_root
        self.key_store = key_store
        self.actor_id = actor_id
        self.restored = None
        self.setWindowTitle("恢复加密备份")
        fit_window_to_available(self, 650, 560, minimum_width=560, minimum_height=460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        title = QLabel("恢复加密备份")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        warning = QLabel(
            "恢复将用备份中的题库、图片、管理员、场次和答题记录替换当前数据。"
            "恢复成功后软件会退出。"
        )
        warning.setObjectName("warningText")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        form = QFormLayout()
        backup_row = QHBoxLayout()
        self.backup_edit = QLineEdit()
        self.backup_edit.setPlaceholderText("选择 .exambackup 文件")
        backup_row.addWidget(self.backup_edit, 1)
        backup_browse = QPushButton("选择")
        backup_browse.clicked.connect(self._choose_backup)
        backup_row.addWidget(backup_browse)
        form.addRow("备份文件", backup_row)

        self.backup_password = QLineEdit()
        self.backup_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("备份密码", self.backup_password)

        certificate_row = QHBoxLayout()
        self.certificate_edit = QLineEdit()
        self.certificate_edit.setPlaceholderText(
            "新电脑恢复时必须选择原管理员导出的 .examtrust 证书"
        )
        certificate_row.addWidget(self.certificate_edit, 1)
        certificate_browse = QPushButton("选择")
        certificate_browse.clicked.connect(self._choose_certificate)
        certificate_row.addWidget(certificate_browse)
        form.addRow("信任证书", certificate_row)

        self.admin_password = QLineEdit()
        self.admin_password.setEchoMode(QLineEdit.EchoMode.Password)
        if actor_id is not None:
            form.addRow("当前主管理员密码", self.admin_password)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setPlaceholderText("必须输入大写 RESTORE")
        form.addRow("英文确认", self.confirm_edit)
        layout.addLayout(form)

        certificate_hint = QLabel(
            "同一套已信任该备份签名的安装可以不再选择证书；新电脑首次恢复必须同时提供证书。"
        )
        certificate_hint.setObjectName("formHint")
        certificate_hint.setWordWrap(True)
        layout.addWidget(certificate_hint)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        restore = QPushButton("验证并恢复")
        restore.setObjectName("dangerButton")
        restore.clicked.connect(self._restore)
        buttons.addButton(restore, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_backup(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择加密备份",
            str(Path.home() / "Desktop"),
            "加密备份 (*.exambackup)",
        )
        if path_text:
            self.backup_edit.setText(path_text)

    def _choose_certificate(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择信任证书",
            str(Path.home() / "Desktop"),
            "考试信任证书 (*.examtrust)",
        )
        if path_text:
            self.certificate_edit.setText(path_text)

    def _restore(self) -> None:
        backup_path = Path(self.backup_edit.text()).expanduser()
        if not backup_path.is_file():
            self.error_label.setText("请选择有效的备份文件")
            return
        if not self.backup_password.text():
            self.error_label.setText("请输入备份密码")
            return
        if self.confirm_edit.text() != "RESTORE":
            self.error_label.setText("英文确认必须输入大写 RESTORE")
            return
        try:
            if self.actor_id is not None:
                AdminRepository(self.database).verify_password(
                    self.actor_id,
                    self.admin_password.text(),
                    supervisor_only=True,
                )
            trusted_signers = self.key_store.trusted_public_keys()
            certificate_text = self.certificate_edit.text().strip()
            if certificate_text:
                certificate_path = Path(certificate_text).expanduser()
                signer_id, public_key, _public_bytes = self.key_store.parse_trust_certificate(
                    certificate_path.read_bytes()
                )
                trusted_signers[signer_id] = public_key
            if not trusted_signers:
                raise ValueError("新电脑恢复必须选择原管理员导出的信任证书")
            if self.actor_id is not None:
                safety_directory = self.database.path.parent / "safety_backups"
                safety_path = safety_directory / "before_restore_{}.exambackup".format(
                    datetime.now().strftime("%Y%m%d_%H%M%S")
                )
                BackupService(self.database, self.asset_root).create(
                    safety_path,
                    password=self.admin_password.text(),
                    key_store=self.key_store,
                    software_version=__version__,
                    automatic=True,
                )
            self.restored = BackupService.restore(
                backup_path,
                password=self.backup_password.text(),
                trusted_signers=trusted_signers,
                target_database=self.database.path,
                target_asset_root=self.asset_root,
            )
        except (OSError, PermissionError, ValueError, OrganizationKeyError) as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()


class RotateRecoveryCodeDialog(QDialog):
    def __init__(self, repository: AdminRepository, actor_id: str, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.actor_id = actor_id
        self.setWindowTitle("重新生成恢复码")
        self.setFixedWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        title = QLabel("重新生成恢复码")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        warning = QLabel(
            "生成成功后，原恢复码立即失效。新恢复码只显示一次，"
            "请在关闭窗口前复制并妥善保管。"
        )
        warning.setObjectName("warningText")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QFormLayout()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("请输入当前主管理员密码")
        self.password_edit.returnPressed.connect(self._rotate)
        form.addRow("主管理员密码", self.password_edit)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setMinimumHeight(20)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        rotate = QPushButton("确认并生成")
        rotate.setObjectName("primaryButton")
        rotate.clicked.connect(self._rotate)
        buttons.addButton(rotate, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _rotate(self) -> None:
        if not self.password_edit.text():
            self.error_label.setText("请输入当前主管理员密码")
            self.password_edit.setFocus()
            return
        try:
            recovery_code = self.repository.rotate_supervisor_recovery_code(
                self.actor_id,
                self.password_edit.text(),
            )
        except (PermissionError, ValueError) as exc:
            self.error_label.setText(str(exc))
            self.password_edit.selectAll()
            self.password_edit.setFocus()
            return
        RecoveryCodeDisplayDialog(recovery_code, self).exec()
        self.accept()


class RecoveryCodeDisplayDialog(QDialog):
    def __init__(self, recovery_code: str, parent=None) -> None:
        super().__init__(parent)
        self.recovery_code = recovery_code
        self.setWindowTitle("请保存新恢复码")
        self.setFixedWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        title = QLabel("新恢复码已生成")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QLabel("原恢复码已经失效。此恢复码关闭后无法再次查看。")
        hint.setObjectName("warningText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        code_row = QHBoxLayout()
        self.recovery_edit = QLineEdit(recovery_code)
        self.recovery_edit.setReadOnly(True)
        self.recovery_edit.setObjectName("recoveryCode")
        code_row.addWidget(self.recovery_edit, 1)
        copy_button = QPushButton("复制")
        copy_button.setObjectName("copyRecoveryCodeButton")
        copy_button.clicked.connect(self._copy)
        code_row.addWidget(copy_button)
        layout.addLayout(code_row)
        close_button = QPushButton("我已妥善保存")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.recovery_code)


class AddAdministratorDialog(QDialog):
    def __init__(self, repository: AdminRepository, actor_id: str, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.actor_id = actor_id
        self.setWindowTitle("添加管理员")
        self.setFixedWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        title = QLabel("添加管理员")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QLabel("这里只登记协作管理员身份；对方导入工作包时自行设置本机登录密码。")
        hint.setObjectName("formHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("姓名", self.name_edit)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._create)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create(self) -> None:
        if not self.name_edit.text().strip():
            self.error_label.setText("姓名不能为空")
            return
        try:
            temporary_password = secrets.token_urlsafe(32)
            self.repository.add_admin(self.actor_id, self.name_edit.text(), temporary_password)
        except (PermissionError, ValueError) as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()


class OrphanAttemptsDialog(QDialog):
    def __init__(self, service: OrphanAttemptService, actor_id: str, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.actor_id = actor_id
        self.items = []
        self.setWindowTitle("无法恢复的考试")
        fit_window_to_available(self, 900, 520, minimum_width=660, minimum_height=400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        title = QLabel("无法恢复的进行中考试")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QLabel("这里只显示状态文件缺失或明显损坏的记录；正常可恢复考试不会显示。")
        hint.setObjectName("pageMeta")
        layout.addWidget(hint)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(("考生", "场次编号", "开始时间", "问题", "状态文件"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        configure_sorting(
            self.table,
            "orphan_attempts",
            default_column=2,
            default_order=Qt.SortOrder.DescendingOrder,
        )
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        void_button = QPushButton("作废所选考试")
        void_button.setObjectName("dangerButton")
        void_button.clicked.connect(self.void_selected)
        buttons.addButton(void_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.items = self.service.list_unrecoverable()
        sort_state = begin_table_update(self.table)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            values = (
                item.candidate_name,
                item.session_id,
                format_local_datetime(item.started_at, empty="-"),
                item.issue,
                item.state_filename or "旧版状态记录",
            )
            sort_values = (
                item.candidate_name.casefold(),
                item.session_id,
                item.started_at,
                item.issue,
                item.state_filename,
            )
            for column, (value, sort_value) in enumerate(zip(values, sort_values, strict=True)):
                self.table.setItem(
                    row,
                    column,
                    sortable_item(
                        value,
                        sort_value=sort_value,
                        identity=item.id if column == 0 else None,
                    ),
                )
        end_table_update(self.table, sort_state)
        if self.items:
            self.table.selectRow(0)

    def void_selected(self) -> None:
        attempt_id = selected_identity(self.table, self.table.currentRow())
        if attempt_id is None:
            return
        item = next((candidate for candidate in self.items if candidate.id == attempt_id), None)
        if item is None:
            return
        dialog = ReauthReasonDialog(
            "作废无法恢复的考试",
            "考生：{}\n开始时间：{}\n原因：{}".format(
                item.candidate_name,
                format_local_datetime(item.started_at, empty="-"),
                item.issue,
            ),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.void(
                item.id,
                actor_id=self.actor_id,
                password=dialog.password,
                reason=dialog.reason,
            )
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.critical(self, "作废失败", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "处理完成", "该考试已作废，现在可以重新开始考试。")


class AuditLogDialog(QDialog):
    ACTION_TEXT = {
        "void_unrecoverable_attempt": "作废无法恢复考试",
        "delete_questions": "删除或停用题目",
        "delete_sessions": "删除或归档场次",
        "delete_attempts": "删除答题记录",
        "batch_update_questions": "批量修改题目",
        "excel_update_question": "Excel更新题目",
        "factory_reset_requested": "请求恢复出厂状态",
        "rotate_supervisor_recovery_code": "重新生成恢复码",
    }

    def __init__(self, database, parent=None) -> None:
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("操作审计")
        fit_window_to_available(self, 1040, 620, minimum_width=700, minimum_height=420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        title = QLabel("操作审计")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        filters = QHBoxLayout()
        self.actor_combo = QComboBox()
        self.actor_combo.addItem("全部管理员", None)
        self.action_combo = QComboBox()
        self.action_combo.addItem("全部操作", None)
        with self.database.connect() as connection:
            admins = connection.execute("SELECT id, name FROM administrators ORDER BY name").fetchall()
            actions = connection.execute("SELECT DISTINCT action FROM audit_events ORDER BY action").fetchall()
        for row in admins:
            self.actor_combo.addItem(row["name"], row["id"])
        for row in actions:
            action = row["action"]
            self.action_combo.addItem(self.ACTION_TEXT.get(action, action), action)
        filters.addWidget(self.actor_combo)
        filters.addWidget(self.action_combo)
        refresh_button = QPushButton("筛选")
        refresh_button.clicked.connect(self.refresh)
        filters.addWidget(refresh_button)
        filters.addStretch(1)
        layout.addLayout(filters)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(("时间", "管理员", "操作", "对象", "详情"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        configure_sorting(
            self.table,
            "audit_log",
            default_column=0,
            default_order=Qt.SortOrder.DescendingOrder,
        )
        layout.addWidget(self.table, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self) -> None:
        clauses = []
        parameters = []
        if self.actor_combo.currentData():
            clauses.append("ae.actor_id = ?")
            parameters.append(self.actor_combo.currentData())
        if self.action_combo.currentData():
            clauses.append("ae.action = ?")
            parameters.append(self.action_combo.currentData())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT ae.created_at, ae.action, ae.entity_type, ae.details_json,
                       COALESCE(a.name, '系统') AS actor_name
                FROM audit_events ae
                LEFT JOIN administrators a ON a.id = ae.actor_id
                """
                + where
                + " ORDER BY ae.created_at DESC LIMIT 500",
                parameters,
            ).fetchall()
        sort_state = begin_table_update(self.table)
        self.table.setRowCount(len(rows))
        for table_row, row in enumerate(rows):
            details = json.loads(row["details_json"] or "{}")
            values = (
                format_local_datetime(row["created_at"], empty="-"),
                row["actor_name"],
                self.ACTION_TEXT.get(row["action"], row["action"]),
                row["entity_type"],
                json.dumps(details, ensure_ascii=False, sort_keys=True),
            )
            sort_values = (
                row["created_at"],
                row["actor_name"].casefold(),
                values[2],
                row["entity_type"],
                values[4],
            )
            for column, (value, sort_value) in enumerate(zip(values, sort_values, strict=True)):
                self.table.setItem(
                    table_row,
                    column,
                    sortable_item(value, sort_value=sort_value),
                )
        end_table_update(self.table, sort_state)


class FactoryResetDialog(QDialog):
    def __init__(
        self,
        service: FactoryResetService,
        key_store: OrganizationKeyStore,
        asset_root: Path,
        actor_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.key_store = key_store
        self.asset_root = asset_root
        self.actor_id = actor_id
        self.staged_directory: Path | None = None
        preview = service.preview()
        self.setWindowTitle("恢复出厂状态")
        self.setFixedWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        title = QLabel("恢复出厂状态")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        impact = QLabel(
            f"将清空：{preview.administrators} 个管理员、{preview.questions} 道题、"
            f"{preview.sessions} 个场次、{preview.attempts} 份答卷、"
            f"{preview.assets} 个图片资源。\n{preview.root}"
        )
        impact.setWordWrap(True)
        impact.setObjectName("warningText")
        layout.addWidget(impact)
        form = QFormLayout()
        backup_row = QHBoxLayout()
        default = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation))
        self.backup_edit = QLineEdit(str(default / "ExamDesk 离线考试系统_重置前备份.exambackup"))
        backup_row.addWidget(self.backup_edit, 1)
        browse = QPushButton("选择")
        browse.clicked.connect(self.choose_backup)
        backup_row.addWidget(browse)
        form.addRow("备份文件", backup_row)
        self.backup_password = QLineEdit()
        self.backup_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("备份密码", self.backup_password)
        self.skip_backup = QCheckBox("跳过备份并永久清空")
        self.skip_backup.toggled.connect(self._backup_toggled)
        form.addRow("", self.skip_backup)
        self.admin_password = QLineEdit()
        self.admin_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("主管理员密码", self.admin_password)
        self.yes_edit = QLineEdit()
        self.yes_edit.setPlaceholderText("必须输入大写 YES")
        form.addRow("英文确认", self.yes_edit)
        self.chinese_edit = QLineEdit()
        self.chinese_edit.setPlaceholderText("必须输入：永久清空")
        form.addRow("中文确认", self.chinese_edit)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        reset_button = QPushButton("永久清空并退出")
        reset_button.setObjectName("dangerButton")
        reset_button.clicked.connect(self.execute_reset)
        buttons.addButton(reset_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_backup(self) -> None:
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "保存重置前备份",
            self.backup_edit.text(),
            "加密备份 (*.exambackup)",
        )
        if path_text:
            self.backup_edit.setText(str(Path(path_text).with_suffix(".exambackup")))

    def _backup_toggled(self, skipped: bool) -> None:
        self.backup_edit.setEnabled(not skipped)
        self.backup_password.setEnabled(not skipped)

    def execute_reset(self) -> None:
        if self.yes_edit.text() != "YES" or self.chinese_edit.text() != "永久清空":
            self.error_label.setText("两项确认文字必须完全一致")
            return
        if not self.admin_password.text():
            self.error_label.setText("请输入主管理员密码")
            return
        skipped = self.skip_backup.isChecked()
        destination = Path(self.backup_edit.text()).expanduser().resolve()
        if not skipped:
            if not self.backup_password.text():
                self.error_label.setText("请输入备份密码")
                return
            if self.service.root == destination or self.service.root in destination.parents:
                self.error_label.setText("重置前备份必须保存在软件数据目录之外")
                return
        try:
            self.service.authenticate_and_record(
                self.actor_id,
                self.admin_password.text(),
                skipped_backup=skipped,
            )
            if not skipped:
                BackupService(self.service.database, self.asset_root).create(
                    destination.with_suffix(".exambackup"),
                    password=self.backup_password.text(),
                    key_store=self.key_store,
                    software_version=__version__,
                )
            self.staged_directory = self.service.stage_reset()
        except (OSError, PermissionError, ValueError, OrganizationKeyError) as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()
