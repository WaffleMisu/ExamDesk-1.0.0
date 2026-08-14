from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from examdesk.domain.enums import QuestionType
from examdesk.packages import PasswordPackageCodec
from examdesk.practice import PracticeDefinition, PracticePackageReader

from .session_management import TYPE_LABELS


class PracticeAccessDialog(QDialog):
    def __init__(self, package_path: Path, trusted_signers: dict, parent=None) -> None:
        super().__init__(parent)
        self.package_path = package_path
        self.trusted_signers = trusted_signers
        self.definition: PracticeDefinition | None = None
        self.package_data: bytes | None = None
        self.package_error = ""
        self.password_required = True
        self.setWindowTitle("打开练习包")
        self.setFixedWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        title = QLabel("打开练习包")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        file_label = QLabel(package_path.name)
        file_label.setObjectName("pageMeta")
        layout.addWidget(file_label)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("练习包密码（可选）")
        try:
            self.package_data = package_path.read_bytes()
            self.password_required = PasswordPackageCodec.requires_password(self.package_data)
        except (OSError, ValueError) as exc:
            self.package_error = str(exc)
        if not self.password_required:
            self.password_edit.setVisible(False)
        self.password_edit.returnPressed.connect(self._open)
        layout.addWidget(self.password_edit)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        open_button = QPushButton("打开")
        open_button.setObjectName("primaryButton")
        open_button.clicked.connect(self._open)
        buttons.addButton(open_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open(self) -> None:
        if self.package_error:
            self.error_label.setText(self.package_error)
            return
        if self.package_data is None:
            self.error_label.setText("练习包无法读取")
            return
        try:
            self.definition = PracticePackageReader.open(
                self.package_data,
                distribution_password=self.password_edit.text() if self.password_required else "",
                trusted_signers=self.trusted_signers,
            )
        except (OSError, ValueError) as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()


class PracticeSetupDialog(QDialog):
    def __init__(self, definition: PracticeDefinition, parent=None) -> None:
        super().__init__(parent)
        self.definition = definition
        self.counts: dict[QuestionType, int] | None = None
        self.setWindowTitle("选择练习题量")
        self.setFixedWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        title = QLabel(definition.name)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.spins = {}
        for index, question_type in enumerate(QuestionType):
            available = sum(
                question.question_type is question_type for question in definition.questions
            )
            label = QLabel(f"{TYPE_LABELS[question_type]}（{available}）")
            spin = QSpinBox()
            spin.setRange(0, available)
            self.spins[question_type] = spin
            grid.addWidget(label, index, 0)
            grid.addWidget(spin, index, 1)
        layout.addLayout(grid)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        start = QPushButton("开始练习")
        start.setObjectName("primaryButton")
        start.clicked.connect(self._accept)
        buttons.addButton(start, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        counts = {question_type: spin.value() for question_type, spin in self.spins.items()}
        if sum(counts.values()) <= 0:
            self.error_label.setText("练习题量合计必须大于0")
            return
        self.counts = counts
        self.accept()
