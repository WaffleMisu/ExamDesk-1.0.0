from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from examdesk.branding import BRAND_RAIL_NAME
from examdesk.version import __version__


class EntryTile(QFrame):
    activated = Signal()

    def __init__(self, title: str, meta: str, action: str, accent_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("entryTile")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(245)
        self.setFixedHeight(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        accent = QFrame()
        accent.setObjectName(accent_name)
        accent.setFixedSize(42, 5)
        layout.addWidget(accent, 0, Qt.AlignmentFlag.AlignLeft)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("entryTitle")
        layout.addWidget(self.title_label)

        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName("entryMeta")
        self.meta_label.setWordWrap(True)
        self.meta_label.setMinimumHeight(42)
        layout.addWidget(self.meta_label)
        layout.addStretch(1)

        self.button = QPushButton(action)
        self.button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.button.clicked.connect(self.activated)
        layout.addWidget(self.button)

    def set_content(self, title: str, meta: str, action: str) -> None:
        self.title_label.setText(title)
        self.meta_label.setText(meta)
        self.button.setText(action)


class HomePage(QWidget):
    admin_requested = Signal()
    exam_requested = Signal()
    practice_requested = Signal()
    collaboration_requested = Signal()
    trust_requested = Signal()
    theme_requested = Signal()

    def __init__(self, *, admin_enabled: bool = True, edition_name: str = "主管理员版", parent=None) -> None:
        super().__init__(parent)
        self.admin_enabled = admin_enabled
        self.edition_name = edition_name
        self.collaboration_entry: EntryTile | None = None
        self.setObjectName("appRoot")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_brand_rail())
        root.addWidget(self._build_entries(), 1)

    def _build_brand_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("brandRail")
        rail.setFixedWidth(286)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(30, 34, 30, 28)
        layout.setSpacing(8)

        name = QLabel(BRAND_RAIL_NAME)
        name.setObjectName("brandName")
        name.setWordWrap(True)
        layout.addWidget(name)

        version = QLabel(f"版本 {__version__} · {self.edition_name}")
        version.setObjectName("brandVersion")
        layout.addWidget(version)
        layout.addStretch(1)

        offline = QLabel("离线运行")
        offline.setObjectName("railMeta")
        layout.addWidget(offline)
        data = QLabel("数据保存在本机")
        data.setObjectName("railMeta")
        layout.addWidget(data)
        return rail

    def _build_entries(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(42, 38, 42, 34)
        layout.setSpacing(10)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(6)
        title = QLabel("选择入口")
        title.setObjectName("pageTitle")
        heading.addWidget(title)
        meta = QLabel("请选择本次要进入的工作区域")
        meta.setObjectName("pageMeta")
        heading.addWidget(meta)
        header.addLayout(heading)
        header.addStretch(1)
        theme_button = QPushButton("外观")
        theme_button.setObjectName("themeButton")
        theme_button.setToolTip("更改主题")
        theme_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        theme_button.clicked.connect(self.theme_requested)
        if not self.admin_enabled:
            self.trust_button = QPushButton("信任证书（0）")
            self.trust_button.setObjectName("trustButton")
            self.trust_button.setToolTip("导入管理员发放的考试信任证书")
            self.trust_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
            )
            self.trust_button.clicked.connect(self.trust_requested)
            header.addWidget(self.trust_button, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(theme_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        layout.addSpacing(22)

        tile_row = QHBoxLayout()
        tile_row.setSpacing(16)
        exam = EntryTile("正式考试", "选择 .exampack 文件", "打开考试包", "entryAccentExam")
        exam.setObjectName("examEntry")
        practice = EntryTile("练习", "选择 .practicepack 文件", "打开练习包", "entryAccentPractice")
        practice.setObjectName("practiceEntry")
        exam.activated.connect(self.exam_requested)
        practice.activated.connect(self.practice_requested)
        tiles = []
        if self.admin_enabled:
            admin = EntryTile("主管理员", "权威题库与考试管理", "主管理员登录", "entryAccentAdmin")
            admin.setObjectName("adminEntry")
            admin.activated.connect(self.admin_requested)
            tiles.append(admin)
        tiles.extend((exam, practice))
        if not self.admin_enabled:
            self.collaboration_entry = EntryTile(
                "协作题库",
                "导入主管理员工作包后使用",
                "导入工作包",
                "entryAccentAdmin",
            )
            self.collaboration_entry.setObjectName("collaborationEntry")
            self.collaboration_entry.activated.connect(self.collaboration_requested)
            tiles.append(self.collaboration_entry)
        for tile in tiles:
            tile_row.addWidget(tile)
        layout.addLayout(tile_row)
        layout.addStretch(1)

        footer = QLabel("Windows 10 x64 · 本地题库 · 本地判分")
        footer.setObjectName("pageMeta")
        layout.addWidget(footer, 0, Qt.AlignmentFlag.AlignRight)
        return content

    def set_collaboration_available(self, available: bool) -> None:
        if self.collaboration_entry is None:
            return
        if available:
            self.collaboration_entry.set_content("协作题库", "已安装工作包，可维护授权题库", "进入协作题库")
        else:
            self.collaboration_entry.set_content("协作题库", "导入主管理员工作包后使用", "导入工作包")

    def set_trust_certificate_count(self, count: int) -> None:
        button = getattr(self, "trust_button", None)
        if button is not None:
            button.setText(f"信任证书（{count}）")

