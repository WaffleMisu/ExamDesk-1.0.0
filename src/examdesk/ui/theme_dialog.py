from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .theme import THEMES, ThemeManager
from .theme_settings import ThemeSettings

ACCENT_SWATCHES = (
    ("清爽蓝", "#2563A6"),
    ("珊瑚", "#D9534F"),
    ("琥珀", "#C17A16"),
    ("青绿", "#008A83"),
    ("经典绿", "#2F6B55"),
    ("湖蓝", "#1479C9"),
    ("石墨蓝", "#6FA8FF"),
)
SCOPE_OPTIONS = (("不使用背景图", "none"), ("仅首页", "home"), ("整个软件", "all"))


class ThemeDialog(QDialog):
    def __init__(self, manager: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.original = manager.settings
        self.selected_background: Path | None = None
        self.background_cleared = False
        self.accent_color = self.original.accent_color
        self.setWindowTitle("外观设置")
        self.setMinimumWidth(590)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)
        title = QLabel("外观设置")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(16)
        self.theme_combo = QComboBox()
        for theme in THEMES.values():
            self.theme_combo.addItem(theme.name, theme.id)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.original.theme_id)))
        self.theme_combo.currentIndexChanged.connect(self._preset_changed)
        form.addRow("预设主题", self.theme_combo)

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(8)
        self.swatch_buttons: list[QPushButton] = []
        for name, color in ACCENT_SWATCHES:
            button = QPushButton()
            button.setFixedSize(32, 32)
            button.setToolTip(name)
            button.clicked.connect(lambda checked=False, value=color: self._set_accent(value))
            button.setStyleSheet(
                f"QPushButton {{ background:{color}; border:2px solid rgba(0,0,0,0.16); "
                "border-radius:5px; padding:0; }"
                "QPushButton:hover { border:3px solid rgba(0,0,0,0.35); }"
            )
            swatch_row.addWidget(button)
            self.swatch_buttons.append(button)
        custom_button = QPushButton("自定义颜色")
        custom_button.clicked.connect(self._choose_custom_color)
        swatch_row.addWidget(custom_button)
        reset_accent = QPushButton("主题默认色")
        reset_accent.clicked.connect(lambda: self._set_accent(None))
        swatch_row.addWidget(reset_accent)
        swatch_row.addStretch(1)
        form.addRow("强调色", swatch_row)

        background_row = QHBoxLayout()
        self.background_label = QLabel(self.original.background_file or "未选择")
        self.background_label.setObjectName("pageMeta")
        self.background_label.setWordWrap(True)
        background_row.addWidget(self.background_label, 1)
        select_background = QPushButton("选择图片")
        select_background.clicked.connect(self._choose_background)
        background_row.addWidget(select_background)
        clear_background = QPushButton("清除")
        clear_background.clicked.connect(self._clear_background)
        background_row.addWidget(clear_background)
        form.addRow("背景图片", background_row)

        self.scope_combo = QComboBox()
        for text, value in SCOPE_OPTIONS:
            self.scope_combo.addItem(text, value)
        self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(self.original.background_scope)))
        self.scope_combo.currentIndexChanged.connect(self._preview)
        self._update_scope_enabled()
        form.addRow("应用范围", self.scope_combo)
        layout.addLayout(form)

        preview = QFrame()
        preview.setObjectName("summaryPanel")
        preview_layout = QHBoxLayout(preview)
        preview_layout.setContentsMargins(18, 16, 18, 16)
        preview_text = QLabel("主题预览")
        preview_text.setObjectName("entryTitle")
        preview_layout.addWidget(preview_text)
        preview_layout.addStretch(1)
        preview_button = QPushButton("主要按钮")
        preview_button.setObjectName("primaryButton")
        preview_layout.addWidget(preview_button)
        layout.addWidget(preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        restore_button = QPushButton("恢复默认")
        restore_button.clicked.connect(self._restore_defaults)
        buttons.addButton(restore_button, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._commit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _preset_changed(self) -> None:
        self.accent_color = None
        self._preview()

    def _set_accent(self, color: str | None) -> None:
        self.accent_color = color
        self._preview()

    def _choose_custom_color(self) -> None:
        initial = QColor(self.accent_color or THEMES[self.theme_combo.currentData()].accent)
        selected = QColorDialog.getColor(initial, self, "选择强调色")
        if selected.isValid():
            self._set_accent(selected.name().upper())

    def _choose_background(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景图片",
            str(Path.home() / "Pictures"),
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)",
        )
        if not path_text:
            return
        self.selected_background = Path(path_text)
        self.background_cleared = False
        self.background_label.setText(self.selected_background.name)
        if self.scope_combo.currentData() == "none":
            self.scope_combo.setCurrentIndex(self.scope_combo.findData("home"))
        self._update_scope_enabled()
        self._preview()

    def _clear_background(self) -> None:
        self.selected_background = None
        self.background_cleared = True
        self.background_label.setText("未选择")
        self.scope_combo.setCurrentIndex(self.scope_combo.findData("none"))
        self._update_scope_enabled()
        self._preview()

    def _restore_defaults(self) -> None:
        self.theme_combo.setCurrentIndex(self.theme_combo.findData("clean_blue"))
        self.accent_color = None
        self.selected_background = None
        self.background_cleared = True
        self.background_label.setText("未选择")
        self.scope_combo.setCurrentIndex(self.scope_combo.findData("none"))
        self._update_scope_enabled()
        self._preview()

    def _settings(self) -> ThemeSettings:
        background_file = "" if self.background_cleared else self.original.background_file
        if self.selected_background is not None:
            background_file = self.selected_background.name
        return ThemeSettings(
            theme_id=self.theme_combo.currentData(),
            accent_color=self.accent_color,
            background_scope=self.scope_combo.currentData(),
            background_file=background_file,
        )

    def _preview(self) -> None:
        self.manager.preview(self._settings(), self.selected_background)

    def _update_scope_enabled(self) -> None:
        has_background = bool(self.original.background_file) or self.selected_background is not None
        if self.background_cleared:
            has_background = False
        self.scope_combo.setEnabled(has_background)

    def _commit(self) -> None:
        settings = self._settings()
        try:
            self.manager.commit(settings, self.selected_background)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "无法保存主题", str(exc))
            return
        self.accept()

    def reject(self) -> None:
        self.manager.restore(self.original)
        super().reject()
