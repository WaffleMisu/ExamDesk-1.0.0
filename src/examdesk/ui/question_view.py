from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from examdesk.domain.enums import QuestionType
from examdesk.domain.models import QuestionDraft

from .image_viewer import ImageStrip


class QuestionView(QFrame):
    response_changed = Signal(object)

    def __init__(
        self,
        question: QuestionDraft,
        option_order: tuple[str, ...],
        response: object,
        assets: dict[str, bytes],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.question = question
        self.option_order = option_order
        self.assets = assets
        self.controls: dict[str, QWidget] = {}
        self.setObjectName("questionSurface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 30)
        layout.setSpacing(16)

        stem = QLabel(question.stem)
        stem.setObjectName("questionStem")
        stem.setWordWrap(True)
        stem.setTextInteractionFlags(stem.textInteractionFlags())
        layout.addWidget(stem)
        question_images = _asset_bytes(question.question_asset_ids, assets)
        if question_images:
            layout.addWidget(ImageStrip(question_images))

        if question.question_type is QuestionType.FILL:
            self._add_fill_controls(layout, response)
        else:
            self._add_choice_controls(layout, response)
        layout.addStretch(1)

    def _add_choice_controls(self, layout: QVBoxLayout, response: object) -> None:
        selected = set(response) if isinstance(response, (list, tuple, set)) else set()
        by_key = {option.key: option for option in self.question.options}
        order = self.option_order or tuple(by_key)
        group = QButtonGroup(self)
        group.setExclusive(self.question.question_type is not QuestionType.MULTIPLE)
        for display_index, key in enumerate(order):
            option = by_key[key]
            display_key = chr(ord("A") + display_index)
            if self.question.question_type is QuestionType.MULTIPLE:
                control = QCheckBox(f"{display_key}. {option.text}")
            else:
                control = QRadioButton(f"{display_key}. {option.text}")
                group.addButton(control)
            control.setObjectName("answerOption")
            control.setProperty("answerKey", key)
            self.controls[key] = control
            control.setChecked(key in selected)
            if self.question.question_type is QuestionType.MULTIPLE:
                control.stateChanged.connect(self._emit_choice_response)
            else:
                control.toggled.connect(self._emit_choice_response)
            layout.addWidget(control)
            option_images = _asset_bytes(option.asset_ids, self.assets)
            if option_images:
                image_row = ImageStrip(option_images)
                image_row.setContentsMargins(28, 0, 0, 4)
                layout.addWidget(image_row)

    def _add_fill_controls(self, layout: QVBoxLayout, response: object) -> None:
        values = list(response) if isinstance(response, (list, tuple)) else []
        for blank in self.question.blanks:
            row = QHBoxLayout()
            label = QLabel(f"（{blank.index}）")
            label.setFixedWidth(48)
            row.addWidget(label)
            edit = QLineEdit()
            edit.setText(str(values[blank.index - 1]) if blank.index <= len(values) else "")
            edit.textChanged.connect(self._emit_fill_response)
            self.controls[str(blank.index)] = edit
            row.addWidget(edit, 1)
            layout.addLayout(row)

    def _emit_choice_response(self) -> None:
        selected = [
            key
            for key in self.option_order or tuple(self.controls)
            if self.controls[key].isChecked()
        ]
        self.response_changed.emit(selected)

    def _emit_fill_response(self) -> None:
        values = [
            self.controls[str(index)].text()
            for index in range(1, len(self.question.blanks) + 1)
        ]
        self.response_changed.emit(values)

    def lock_for_review(self, response: object) -> None:
        selected = set(response) if isinstance(response, (list, tuple, set)) else set()
        for key, control in self.controls.items():
            state = ""
            if self.question.question_type is not QuestionType.FILL:
                if key in self.question.correct_option_keys:
                    state = "correct"
                elif key in selected:
                    state = "wrong"
            control.setProperty("reviewState", state)
            control.setEnabled(False)
            control.style().unpolish(control)
            control.style().polish(control)


def _asset_bytes(asset_ids, assets: dict[str, bytes]) -> list[bytes]:
    return [assets[asset_id] for asset_id in asset_ids if asset_id in assets]
