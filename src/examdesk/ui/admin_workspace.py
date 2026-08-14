from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from examdesk.db import Administrator, Database
from examdesk.practice import PracticeService
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.security import OrganizationKeyStore
from examdesk.sessions import SessionService

from .question_bank import QuestionBankPage
from .result_management import ResultManagementPage
from .session_management import SessionManagementPage
from .system_maintenance import SystemMaintenancePage


class MetricPanel(QFrame):
    def __init__(self, label: str, value: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("summaryPanel")
        self.setMinimumHeight(112)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        value_label = QLabel(value)
        value_label.setObjectName("metricValue")
        layout.addWidget(value_label)
        label_widget = QLabel(label)
        label_widget.setObjectName("metricLabel")
        layout.addWidget(label_widget)


class AdminWorkspace(QWidget):
    home_requested = Signal()

    def __init__(
        self,
        database: Database,
        administrator: Administrator,
        asset_root,
        key_store: OrganizationKeyStore,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self.administrator = administrator
        self.key_store = key_store
        self.question_repository = QuestionRepository(database)
        self.asset_manager = AssetManager(database, asset_root)
        self.session_service = SessionService(
            database,
            self.question_repository,
            self.asset_manager,
        )
        self.practice_service = PracticeService(
            database,
            self.question_repository,
            self.asset_manager,
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        nav, self.nav_group = self._build_navigation()
        body.addWidget(nav)
        self.pages = QStackedWidget()
        self.page_factories: list[Callable[[], QWidget]] = [
            self._overview_page,
            lambda: QuestionBankPage(
                self.question_repository,
                self.asset_manager,
                self.administrator.id,
                self.practice_service,
                self.key_store,
                self.administrator.role,
            ),
            lambda: SessionManagementPage(
                self.session_service,
                self.key_store,
                self.administrator.id,
                self.administrator.role,
            ),
            lambda: ResultManagementPage(
                self.database,
                self.key_store,
                self.administrator.id,
                self.administrator.role,
            ),
            lambda: SystemMaintenancePage(
                self.key_store,
                self.database,
                self.asset_manager.root,
                self.administrator,
            ),
        ]
        for factory in self.page_factories:
            self.pages.addWidget(factory())
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(66)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(22, 0, 22, 0)
        back = QPushButton()
        back.setToolTip("返回入口")
        back.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        back.setFixedSize(40, 40)
        back.clicked.connect(self.home_requested)
        layout.addWidget(back)
        title = QLabel("管理中心")
        title.setObjectName("entryTitle")
        layout.addWidget(title)
        layout.addStretch(1)
        identity = QLabel(f"{self.administrator.name} · {self._role_text()}")
        identity.setObjectName("pageMeta")
        layout.addWidget(identity)
        return bar

    def _build_navigation(self) -> tuple[QWidget, QButtonGroup]:
        nav = QWidget()
        nav.setObjectName("adminNav")
        nav.setFixedWidth(220)
        layout = QVBoxLayout(nav)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(6)
        group = QButtonGroup(nav)
        group.setExclusive(True)
        entries = ("概览", "题库", "考试场次", "答题记录", "系统维护")
        for index, text in enumerate(entries):
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=index: self.pages.setCurrentIndex(page))
            group.addButton(button, index)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        layout.addStretch(1)
        return nav, group

    def _overview_page(self) -> QWidget:
        counts = self._summary_counts()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 30)
        layout.setSpacing(18)
        title = QLabel("概览")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        for label, value in counts:
            metrics.addWidget(MetricPanel(label, str(value)))
        layout.addLayout(metrics)
        layout.addStretch(1)
        return page

    def _empty_page(self, title_text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 30)
        title = QLabel(title_text)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        layout.addStretch(1)
        return page

    def _summary_counts(self) -> tuple[tuple[str, int], ...]:
        with self.database.connect() as connection:
            questions = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            sessions = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            results = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE status = 'submitted'"
            ).fetchone()[0]
        return (("题库题目", questions), ("考试场次", sessions), ("已收答卷", results))

    def _role_text(self) -> str:
        return "主管理员" if self.administrator.role.value == "supervisor" else "管理员"
