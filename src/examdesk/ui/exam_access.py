from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from examdesk.packages import PasswordPackageCodec
from examdesk.results import SubmittedReview, SubmittedReviewStore
from examdesk.sessions import ExamDefinition, ExamPackageReader
from examdesk.version import __version__


class ExamAccessDialog(QDialog):
    def __init__(
        self,
        package_path: Path,
        trusted_signers: dict,
        review_store: SubmittedReviewStore,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.package_path = package_path
        self.trusted_signers = trusted_signers
        self.review_store = review_store
        self.definition: ExamDefinition | None = None
        self.submitted_review: SubmittedReview | None = None
        self.candidate_name = ""
        self.package_data: bytes | None = None
        self.package_error = ""
        self.password_required = True
        try:
            self.package_data = package_path.read_bytes()
            self.password_required = PasswordPackageCodec.requires_password(self.package_data)
        except (OSError, ValueError) as exc:
            self.package_error = str(exc)
        self.setWindowTitle("进入正式考试")
        self.setFixedWidth(470)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)
        title = QLabel("进入正式考试")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        package_label = QLabel(package_path.name)
        package_label.setObjectName("pageMeta")
        layout.addWidget(package_label)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("姓名")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("本场考试密码")
        self.password_edit.returnPressed.connect(self._open_package)
        form.addRow("姓名", self.name_edit)
        self.password_label = QLabel("考试密码")
        form.addRow(self.password_label, self.password_edit)
        if not self.password_required:
            self.password_label.setVisible(False)
            self.password_edit.setVisible(False)
        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setMinimumHeight(20)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        enter = QPushButton("验证并进入")
        if not self.password_required:
            enter.setText("进入考试")
        enter.setObjectName("primaryButton")
        enter.clicked.connect(self._open_package)
        buttons.addButton(enter, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_package(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.error_label.setText("姓名不能为空")
            self.name_edit.setFocus()
            return
        if self.package_error:
            self.error_label.setText(self.package_error)
            return
        if self.package_data is None:
            self.error_label.setText("考试包无法读取")
            return
        password = self.password_edit.text() if self.password_required else ""
        if self.password_required and not password:
            self.error_label.setText("考试密码不能为空")
            self.password_edit.setFocus()
            return
        try:
            definition = ExamPackageReader.open(
                self.package_data,
                password=password,
                trusted_signers=self.trusted_signers,
                current_software_version=__version__,
            )
            self.candidate_name = definition.validate_candidate(name)
            self.definition = definition
            try:
                self.submitted_review = self.review_store.load_latest(definition, self.candidate_name)
            except FileNotFoundError:
                self.submitted_review = None
        except (OSError, ValueError) as exc:
            self.error_label.setText(str(exc))
            self.password_edit.selectAll()
            return
        self.accept()


class MonitoringConsentDialog(QDialog):
    def __init__(self, session_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("考试监控告知")
        self.setModal(True)
        self.setFixedWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        title = QLabel("本场考试已启用切屏监控")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        session_label = QLabel(session_name)
        session_label.setObjectName("pageMeta")
        session_label.setWordWrap(True)
        layout.addWidget(session_label)
        notice = QLabel(
            "考试期间，当你切换到其他窗口时，软件会记录：\n"
            "• 软件名称和进程名称\n"
            "• 当前窗口标题\n"
            "• 切出和返回时间及持续时长\n\n"
            "窗口标题可能包含正在打开的文件名或聊天标题。记录将随答题结果保存，"
            "供考试管理员查看；退出本提示不会创建答题状态。"
        )
        notice.setObjectName("warningText")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("不同意并返回")
        consent = QPushButton("我已了解并开始考试")
        consent.setObjectName("monitoringConsentButton")
        consent.clicked.connect(self.accept)
        buttons.addButton(consent, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
