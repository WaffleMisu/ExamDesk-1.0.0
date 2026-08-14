from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from examdesk.domain.enums import ReviewPolicy
from examdesk.questions import question_from_payload
from examdesk.results import SubmittedReview

from .review_question import ReviewQuestionPanel


class ExamResultPage(QWidget):
    home_requested = Signal()

    def __init__(
        self,
        review: SubmittedReview,
        assets: dict[str, bytes] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.review = review
        self.assets = assets or {}
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 28)
        root.setSpacing(18)

        header = QHBoxLayout()
        title = QLabel("考试结果")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        home = QPushButton("返回入口")
        home.clicked.connect(self.home_requested)
        header.addWidget(home)
        root.addLayout(header)

        scores = QHBoxLayout()
        scores.setSpacing(14)
        scores.addWidget(_score_panel("当前得分", f"{review.strict_score} / {review.max_score}"))
        if review.estimated_score != review.strict_score:
            scores.addWidget(_score_panel("相似答案预估最高分", str(review.estimated_score)))
        scores.addStretch(1)
        root.addLayout(scores)

        if review.details_visible:
            root.addWidget(self._details(), 1)
        else:
            notice = QLabel(self._locked_message())
            notice.setObjectName("examWarning")
            notice.setWordWrap(True)
            root.addWidget(notice)
            root.addStretch(1)

    def _details(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)
        for index, item in enumerate(self.review.questions, start=1):
            layout.addWidget(_result_item(index, item, self.assets))
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _locked_message(self) -> str:
        if self.review.policy is ReviewPolicy.SCORE_ONLY:
            return "本场考试设置为只显示成绩。"
        if self.review.release_at is not None:
            return f"正确答案和依据将在 {self.review.release_at.astimezone().strftime('%Y-%m-%d %H:%M')} 后开放。"
        return "正确答案和依据暂未开放。"


def _score_panel(label: str, value: str) -> QFrame:
    panel = QFrame()
    panel.setObjectName("summaryPanel")
    panel.setMinimumWidth(230)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(20, 15, 20, 15)
    value_label = QLabel(value)
    value_label.setObjectName("metricValue")
    layout.addWidget(value_label)
    text = QLabel(label)
    text.setObjectName("metricLabel")
    layout.addWidget(text)
    return panel


def _result_item(index: int, item: dict, assets: dict[str, bytes]) -> ReviewQuestionPanel:
    question = question_from_payload(item["snapshot"])
    return ReviewQuestionPanel(
        index,
        question,
        item.get("response"),
        assets,
        strict_score=item.get("strict_score", "0"),
        estimated_score=item.get("estimated_score"),
        option_order=tuple(item.get("option_order", ())),
    )
