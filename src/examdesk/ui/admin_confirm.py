from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class ReauthReasonDialog(QDialog):
    def __init__(self, title: str, impact_text: str, parent=None) -> None:
        super().__init__(parent)
        self.password = ""
        self.reason = ""
        self.setWindowTitle(title)
        self.setFixedWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        impact = QLabel(impact_text)
        impact.setWordWrap(True)
        impact.setObjectName("warningText")
        layout.addWidget(impact)
        form = QFormLayout()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.reason_edit = QLineEdit()
        form.addRow("当前管理员密码", self.password_edit)
        form.addRow("操作原因", self.reason_edit)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        confirm = QPushButton("确认执行")
        confirm.setObjectName("dangerButton")
        confirm.clicked.connect(self._accept)
        buttons.addButton(confirm, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.password = self.password_edit.text()
        self.reason = self.reason_edit.text().strip()
        if not self.password:
            self.error_label.setText("请输入当前管理员密码")
            return
        if not self.reason:
            self.error_label.setText("请输入操作原因")
            return
        self.accept()
