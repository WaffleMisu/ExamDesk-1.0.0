from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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

from examdesk.db import Administrator, AdminRepository
from examdesk.domain.enums import AdminRole
from examdesk.security.passwords import generate_recovery_code, hash_secret


class AdminLoginDialog(QDialog):
    def __init__(
        self,
        repository: AdminRepository,
        parent=None,
        *,
        required_role: AdminRole | None = None,
        allow_recovery: bool = True,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.required_role = required_role
        self.administrator: Administrator | None = None
        self.setWindowTitle("管理员登录")
        self.setModal(True)
        self.setFixedWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)
        title = QLabel("管理员登录")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("姓名")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("密码")
        self.password_edit.returnPressed.connect(self._authenticate)
        form.addRow("姓名", self.name_edit)
        form.addRow("密码", self.password_edit)
        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setMinimumHeight(20)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        if allow_recovery:
            recovery = QPushButton("恢复主管理员密码")
            recovery.setObjectName("quietButton")
            recovery.clicked.connect(self._open_recovery)
            buttons.addButton(recovery, QDialogButtonBox.ButtonRole.ActionRole)
        login = QPushButton("登录")
        login.setObjectName("primaryButton")
        login.clicked.connect(self._authenticate)
        buttons.addButton(login, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _authenticate(self) -> None:
        administrator = self.repository.authenticate(
            self.name_edit.text(),
            self.password_edit.text(),
        )
        if administrator is None or (
            self.required_role is not None and administrator.role is not self.required_role
        ):
            self.error_label.setText("姓名或密码不正确")
            self.password_edit.selectAll()
            self.password_edit.setFocus()
            return
        self.administrator = administrator
        self.accept()

    def _open_recovery(self) -> None:
        ResetSupervisorDialog(self.repository, self).exec()


class FirstAdminDialog(QDialog):
    def __init__(self, repository: AdminRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.administrator: Administrator | None = None
        self.restore_requested = False
        self.recovery_code = generate_recovery_code()
        self.setWindowTitle("首次初始化")
        self.setModal(True)
        self.setFixedWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 28, 30, 26)
        layout.setSpacing(16)
        title = QLabel("创建主管理员")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)
        self.name_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("姓名", self.name_edit)
        form.addRow("密码", self.password_edit)
        form.addRow("确认密码", self.confirm_edit)
        layout.addLayout(form)

        recovery_panel = QWidget()
        recovery_layout = QHBoxLayout(recovery_panel)
        recovery_layout.setContentsMargins(0, 4, 0, 4)
        recovery_label = QLabel("系统恢复码")
        recovery_layout.addWidget(recovery_label)
        self.recovery_edit = QLineEdit(self.recovery_code)
        self.recovery_edit.setReadOnly(True)
        self.recovery_edit.setObjectName("recoveryCode")
        recovery_layout.addWidget(self.recovery_edit, 1)
        copy_button = QPushButton("复制")
        copy_button.setFixedWidth(72)
        copy_button.clicked.connect(self._copy_recovery_code)
        recovery_layout.addWidget(copy_button)
        layout.addWidget(recovery_panel)

        hint = QLabel("恢复码只在初始化时显示，请由主管理员单独保管。")
        hint.setObjectName("formHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setMinimumHeight(20)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        restore = QPushButton("从加密备份恢复")
        restore.setObjectName("restoreBackupButton")
        restore.clicked.connect(self._request_restore)
        buttons.addButton(restore, QDialogButtonBox.ButtonRole.ActionRole)
        create = QPushButton("完成初始化")
        create.setObjectName("primaryButton")
        create.clicked.connect(self._create)
        buttons.addButton(create, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _request_restore(self) -> None:
        self.restore_requested = True
        self.reject()

    def _copy_recovery_code(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.recovery_code)

    def _create(self) -> None:
        name = self.name_edit.text().strip()
        password = self.password_edit.text()
        if not name:
            self._show_error("姓名不能为空", self.name_edit)
            return
        if len(password) < 8:
            self._show_error("密码至少需要8个字符", self.password_edit)
            return
        if password != self.confirm_edit.text():
            self._show_error("两次输入的密码不一致", self.confirm_edit)
            return
        try:
            self.administrator = self.repository.create_first_admin(
                name,
                password,
                hash_secret(self.recovery_code).encode(),
            )
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()

    def _show_error(self, message: str, widget: QWidget) -> None:
        self.error_label.setText(message)
        widget.setFocus(Qt.FocusReason.OtherFocusReason)


class ResetSupervisorDialog(QDialog):
    def __init__(self, repository: AdminRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.setWindowTitle("恢复主管理员密码")
        self.setFixedWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        title = QLabel("恢复主管理员密码")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.code_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("系统恢复码", self.code_edit)
        form.addRow("新密码", self.password_edit)
        form.addRow("确认密码", self.confirm_edit)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._reset)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset(self) -> None:
        password = self.password_edit.text()
        if len(password) < 8:
            self.error_label.setText("新密码至少需要8个字符")
            return
        if password != self.confirm_edit.text():
            self.error_label.setText("两次输入的密码不一致")
            return
        try:
            self.repository.reset_supervisor_password(self.code_edit.text().strip(), password)
        except (PermissionError, ValueError) as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()
