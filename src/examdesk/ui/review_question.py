from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from examdesk.domain.enums import QuestionType
from examdesk.domain.models import QuestionDraft

from .image_viewer import ImageStrip


class ReviewQuestionPanel(QFrame):
    def __init__(
        self,
        index: int,
        question: QuestionDraft,
        response: object,
        assets: dict[str, bytes],
        *,
        strict_score: Decimal | str | None = None,
        estimated_score: Decimal | str | None = None,
        option_order: tuple[str, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("summaryPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(9)

        score_text = ""
        if strict_score is not None:
            score_text = f"    {strict_score} / {question.score} 分"
        title = QLabel(f"{index}. {question.stem}{score_text}")
        title.setObjectName("entryTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        _add_images(layout, question.question_asset_ids, assets)

        if question.question_type is not QuestionType.FILL:
            _add_choice_options(layout, question, response, assets, option_order)

        answer = QLabel("你的答案：" + format_response(question, response))
        answer.setWordWrap(True)
        layout.addWidget(answer)
        correct = QLabel("正确答案：" + correct_answer(question))
        correct.setWordWrap(True)
        layout.addWidget(correct)
        if estimated_score is not None and strict_score is not None:
            estimated = Decimal(str(estimated_score))
            strict = Decimal(str(strict_score))
            if estimated > strict:
                estimate = QLabel(f"相似答案预估最高分：{estimated} 分，需管理员复核")
                estimate.setObjectName("warningText")
                estimate.setWordWrap(True)
                layout.addWidget(estimate)
        if question.basis:
            basis = QLabel("依据：" + question.basis)
            basis.setObjectName("pageMeta")
            basis.setWordWrap(True)
            basis.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(basis)


def _add_choice_options(
    layout: QVBoxLayout,
    question: QuestionDraft,
    response: object,
    assets: dict[str, bytes],
    option_order: tuple[str, ...],
) -> None:
    selected = set(response) if isinstance(response, (list, tuple, set)) else set()
    options = {option.key: option for option in question.options}
    order = option_order or tuple(options)
    for display_index, key in enumerate(order):
        option = options.get(key)
        if option is None:
            continue
        display_key = chr(ord("A") + display_index)
        suffix = "（你的选择）" if key in selected else ""
        label = QLabel(f"{display_key}. {option.text}{suffix}")
        if key in question.correct_option_keys:
            label.setObjectName("reviewOptionCorrect")
        elif key in selected:
            label.setObjectName("reviewOptionWrong")
        else:
            label.setObjectName("reviewOption")
        label.setWordWrap(True)
        layout.addWidget(label)
        _add_images(layout, option.asset_ids, assets, left_margin=28)


def _add_images(
    layout: QVBoxLayout,
    asset_ids,
    assets: dict[str, bytes],
    *,
    left_margin: int = 0,
) -> None:
    images = [assets[asset_id] for asset_id in asset_ids if asset_id in assets]
    if not images:
        return
    try:
        strip = ImageStrip(images)
    except ValueError:
        warning = QLabel("部分图片无法显示")
        warning.setObjectName("warningText")
        layout.addWidget(warning)
        return
    strip.setContentsMargins(left_margin, 0, 0, 4)
    layout.addWidget(strip)


def format_response(question: QuestionDraft, response: object) -> str:
    if question.question_type is QuestionType.FILL:
        values = response if isinstance(response, (list, tuple)) else []
        return "；".join(
            f"（{index}）{value}" for index, value in enumerate(values, start=1)
        ) or "未作答"
    selected = set(response) if isinstance(response, (list, tuple, set)) else set()
    options = {option.key: option.text for option in question.options}
    return "；".join(options[key] for key in sorted(selected) if key in options) or "未作答"


def correct_answer(question: QuestionDraft) -> str:
    if question.question_type is QuestionType.FILL:
        return "；".join(
            f"（{blank.index}）{' / '.join(blank.accepted_answers)}"
            for blank in question.blanks
        )
    options = {option.key: option.text for option in question.options}
    return "；".join(
        options[key] for key in sorted(question.correct_option_keys) if key in options
    )
