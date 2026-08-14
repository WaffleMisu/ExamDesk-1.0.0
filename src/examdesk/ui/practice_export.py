from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from examdesk.practice import PracticeFilter, PracticeService
from examdesk.version import __version__

from .similarity_settings import SimilaritySettingsControl


class ExportPracticeDialog(QDialog):
    def __init__(self, service: PracticeService, key_store, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.key_store = key_store
        self.setWindowTitle("导出练习包")
        self.setFixedWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        title = QLabel("导出练习包")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.name_edit = QLineEdit("练习题库")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("留空则打开时无需密码")
        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 2100)
        self.year_spin.setSpecialValueText("不限")
        self.chapter_edit = QLineEdit()
        self.chapter_edit.setPlaceholderText("多个章节用英文分号隔开")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("多个标签用英文分号隔开")
        form.addRow("练习包名称", self.name_edit)
        form.addRow("练习包密码（可选）", self.password_edit)
        form.addRow("适用年度", self.year_spin)
        form.addRow("章节筛选", self.chapter_edit)
        form.addRow("标签筛选", self.tags_edit)
        self.similarity_settings = SimilaritySettingsControl()
        form.addRow("填空相似度", self.similarity_settings)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        export = QPushButton("生成练习包")
        export.setObjectName("primaryButton")
        export.clicked.connect(self._export)
        buttons.addButton(export, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _export(self) -> None:
        try:
            package = self.service.export_package(
                name=self.name_edit.text(),
                practice_filter=PracticeFilter(
                    applicable_year=self.year_spin.value() or None,
                    chapters=frozenset(_split(self.chapter_edit.text())),
                    tags=frozenset(_split(self.tags_edit.text())),
                ),
                distribution_password=self.password_edit.text(),
                signer=self.key_store.load().signing,
                minimum_software_version=__version__,
                similarity_level=self.similarity_settings.level,
                custom_similarity_threshold=self.similarity_settings.custom_threshold,
            )
        except (OSError, ValueError) as exc:
            self.error_label.setText(str(exc))
            return
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "保存练习包",
            str(Path(desktop) / f"{self.name_edit.text().strip() or '练习题库'}.practicepack"),
            "练习包 (*.practicepack)",
        )
        if not path_text:
            return
        path = Path(path_text)
        if path.suffix.lower() != ".practicepack":
            path = path.with_suffix(".practicepack")
        try:
            path.write_bytes(package)
        except OSError as exc:
            self.error_label.setText(str(exc))
            return
        QMessageBox.information(self, "导出完成", f"练习包已保存：\n{path}")
        self.accept()


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.replace(",", ";").split(";") if part.strip()]
