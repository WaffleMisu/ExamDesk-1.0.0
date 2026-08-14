from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from examdesk.db import Administrator, Database
from examdesk.domain.enums import AdminRole
from examdesk.questions import AssetManager, BankCollaborationService, QuestionRepository

from .question_bank import QuestionBankPage


class PasswordPairDialog(QDialog):
    def __init__(self, title: str, first_label: str, second_label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        title_label = QLabel(title)
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)
        self.form = QFormLayout()
        self.first_edit = QLineEdit()
        self.first_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.second_edit = QLineEdit()
        self.second_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.form.addRow(first_label, self.first_edit)
        self.form.addRow(second_label, self.second_edit)
        layout.addLayout(self.form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = QPushButton("确定")
        ok.setObjectName("primaryButton")
        ok.clicked.connect(self.accept_values)
        buttons.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def first_value(self) -> str:
        return self.first_edit.text()

    @property
    def second_value(self) -> str:
        return self.second_edit.text()

    def accept_values(self) -> None:
        if len(self.first_value) < 8:
            self.error_label.setText("密码至少需要8个字符")
            self.first_edit.setFocus()
            return
        if self.first_value != self.second_value:
            self.error_label.setText("两次输入的密码不一致")
            self.second_edit.selectAll()
            self.second_edit.setFocus()
            return
        self.accept()


class InstallWorkPackageDialog(PasswordPairDialog):
    def __init__(self, package_path: Path, parent=None) -> None:
        super().__init__("导入协作工作包", "工作包密码", "本机登录密码", parent)
        self.package_path = package_path
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.form.addRow("确认登录密码", self.confirm_edit)
        hint = QLabel("工作包密码只用于解包；本机登录密码由协作管理员自行设置。")
        hint.setObjectName("formHint")
        hint.setWordWrap(True)
        self.layout().insertWidget(1, hint)

    def accept_values(self) -> None:
        if not self.first_value:
            self.error_label.setText("请输入工作包密码")
            self.first_edit.setFocus()
            return
        if len(self.second_value) < 8:
            self.error_label.setText("本机登录密码至少需要8个字符")
            self.second_edit.setFocus()
            return
        if self.second_value != self.confirm_edit.text():
            self.error_label.setText("两次输入的本机登录密码不一致")
            self.confirm_edit.selectAll()
            self.confirm_edit.setFocus()
            return
        self.accept()


class IssueWorkPackageDialog(QDialog):
    def __init__(self, admins: list[Administrator], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("签发协作工作包")
        self.setModal(True)
        self.setFixedWidth(560)
        self.path: Path | None = None
        self.selected_admin: Administrator | None = None
        self.package_password = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        title = QLabel("签发协作工作包")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.admin_combo = QComboBox()
        for item in admins:
            if item.role is AdminRole.ADMIN and item.is_active:
                self.admin_combo.addItem(item.name, item)
        form.addRow("协作管理员", self.admin_combo)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("工作包密码", self.password_edit)
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("确认密码", self.confirm_edit)
        layout.addLayout(form)
        hint = QLabel("工作包包含当前题库和图片。请将工作包文件与密码分开传递。")
        hint.setObjectName("formHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = QPushButton("选择保存位置并签发")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.accept_values)
        buttons.addButton(save, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept_values(self) -> None:
        admin = self.admin_combo.currentData()
        password = self.password_edit.text()
        if admin is None:
            self.error_label.setText("没有可签发的普通管理员")
            return
        if len(password) < 8:
            self.error_label.setText("工作包密码至少需要8个字符")
            return
        if password != self.confirm_edit.text():
            self.error_label.setText("两次输入的密码不一致")
            return
        self.selected_admin = admin
        self.package_password = password
        self.accept()


class CollaborationWorkspace(QWidget):
    home_requested = Signal()
    replace_package_requested = Signal()

    def __init__(
        self,
        database: Database,
        administrator: Administrator,
        asset_root: Path,
        collaboration_service: BankCollaborationService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.setContentsMargins(22, 14, 22, 10)
        back = QPushButton("返回入口")
        back.clicked.connect(self.home_requested)
        top.addWidget(back)
        title = QLabel(f"协作题库 · {administrator.name}")
        title.setObjectName("entryTitle")
        top.addWidget(title)
        top.addStretch(1)
        export_button = QPushButton("导出题库变更包")
        export_button.setObjectName("primaryButton")
        export_button.clicked.connect(lambda: self.export_patch(collaboration_service))
        top.addWidget(export_button)
        replace_button = QPushButton("安装新工作包")
        replace_button.clicked.connect(self.replace_package_requested)
        top.addWidget(replace_button)
        root.addLayout(top)
        self.page = QuestionBankPage(
            QuestionRepository(database),
            AssetManager(database, asset_root),
            administrator.id,
            administrator_role=AdminRole.ADMIN,
        )
        root.addWidget(self.page, 1)

    def export_patch(self, service: BankCollaborationService) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出题库变更包",
            str(Path.home() / "Desktop" / "题库变更包.bankpatch"),
            "题库变更包 (*.bankpatch)",
        )
        if not path_text:
            return
        path = Path(path_text).with_suffix(".bankpatch")
        try:
            path.write_bytes(service.export_patch())
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"题库变更包已保存：\n{path}")
