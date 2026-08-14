from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from examdesk.practice import (
    PracticeGrade,
    PracticeQuestionGrade,
    PracticeService,
    PracticeSession,
)

from .question_view import QuestionView
from .review_question import ReviewQuestionPanel, correct_answer, format_response
from .theme import current_palette


class PracticeRunnerPage(QWidget):
    finished = Signal(object, object)
    home_requested = Signal()

    def __init__(self, session: PracticeSession, service: PracticeService, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.service = service
        self.current_index = 0
        self.marked_ids: set[str] = set()
        self.checked_grades: dict[str, PracticeQuestionGrade] = {}
        self.number_buttons: list[QPushButton] = []
        self.current_view: QuestionView | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top_bar())
        body = QHBoxLayout()
        body.setContentsMargins(24, 20, 0, 20)
        body.setSpacing(20)
        body.addWidget(self._question_area(), 1)
        body.addWidget(self._answer_card())
        root.addLayout(body, 1)
        self.show_question()

    def _top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("examTopBar")
        bar.setFixedHeight(68)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        title = QLabel(self.session.definition.name)
        title.setObjectName("entryTitle")
        layout.addWidget(title)
        mode = QLabel("练习")
        mode.setObjectName("pageMeta")
        layout.addWidget(mode)
        layout.addStretch(1)
        exit_button = QPushButton("退出练习")
        exit_button.clicked.connect(self._exit)
        layout.addWidget(exit_button)
        return bar

    def _question_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        self.position_label = QLabel()
        self.position_label.setObjectName("entryTitle")
        heading.addWidget(self.position_label)
        heading.addStretch(1)
        self.mark_button = QPushButton("标记本题")
        self.mark_button.setCheckable(True)
        self.mark_button.clicked.connect(self._toggle_mark)
        heading.addWidget(self.mark_button)
        layout.addLayout(heading)
        self.question_scroll = QScrollArea()
        self.question_scroll.setWidgetResizable(True)
        layout.addWidget(self.question_scroll, 1)
        nav = QHBoxLayout()
        self.previous_button = QPushButton("上一题")
        self.previous_button.clicked.connect(lambda: self.jump_to(self.current_index - 1))
        nav.addWidget(self.previous_button)
        nav.addStretch(1)
        self.check_button = QPushButton("核对本题")
        self.check_button.setObjectName("primaryButton")
        self.check_button.clicked.connect(self.check_current)
        nav.addWidget(self.check_button)
        self.next_button = QPushButton("下一题")
        self.next_button.clicked.connect(lambda: self.jump_to(self.current_index + 1))
        nav.addWidget(self.next_button)
        layout.addLayout(nav)
        return area

    def _answer_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("answerCard")
        card.setFixedWidth(310)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)
        title = QLabel("答题卡")
        title.setObjectName("entryTitle")
        layout.addWidget(title)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("pageMeta")
        layout.addWidget(self.progress_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setSpacing(8)
        for index in range(len(self.session.questions)):
            button = QPushButton(str(index + 1))
            button.setObjectName("numberButton")
            button.clicked.connect(lambda checked=False, value=index: self.jump_to(value))
            grid.addWidget(button, index // 5, index % 5)
            self.number_buttons.append(button)
        grid.setRowStretch((len(self.number_buttons) + 4) // 5, 1)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        submit = QPushButton("完成练习")
        submit.clicked.connect(self.submit)
        layout.addWidget(submit)
        return card

    def show_question(self, *, scroll_to_feedback: bool = False) -> None:
        question = self.session.questions[self.current_index]
        view = QuestionView(
            question,
            tuple(option.key for option in question.options),
            self.session.responses.get(question.id),
            self.session.definition.assets,
        )
        view.response_changed.connect(
            lambda response, question_id=question.id: self._set_response(question_id, response)
        )
        grade = self.checked_grades.get(question.id)
        if grade is not None:
            view.lock_for_review(self.session.responses.get(question.id))
        self.current_view = view

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addWidget(view)
        if grade is not None:
            content_layout.addWidget(
                _practice_feedback(
                    question,
                    self.session.responses.get(question.id),
                    grade,
                )
            )
        content_layout.addStretch(1)
        old = self.question_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.question_scroll.setWidget(content)
        scroll_bar = self.question_scroll.verticalScrollBar()
        if scroll_to_feedback:
            QTimer.singleShot(0, lambda: scroll_bar.setValue(scroll_bar.maximum()))
        else:
            scroll_bar.setValue(0)
        self.position_label.setText(
            f"第 {self.current_index + 1} 题 / 共 {len(self.session.questions)} 题"
        )
        self.mark_button.blockSignals(True)
        self.mark_button.setChecked(question.id in self.marked_ids)
        self.mark_button.blockSignals(False)
        self.previous_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index + 1 < len(self.session.questions))
        self.check_button.setEnabled(grade is None)
        self.check_button.setText("已核对" if grade is not None else "核对本题")
        self._refresh_card()

    def jump_to(self, index: int) -> None:
        if 0 <= index < len(self.session.questions):
            self.current_index = index
            self.show_question()

    def _set_response(self, question_id: str, response: object) -> None:
        if question_id in self.checked_grades:
            return
        if _empty(response):
            self.session.responses.pop(question_id, None)
        else:
            self.session.set_response(question_id, response)
        self._refresh_card()

    def check_current(self) -> None:
        question = self.session.questions[self.current_index]
        response = self.session.responses.get(question.id)
        if _empty(response):
            QMessageBox.information(self, "核对本题", "请先完成本题。")
            return
        if question.id not in self.checked_grades:
            self.checked_grades[question.id] = self.session.grade_question(question.id)
        self.show_question(scroll_to_feedback=True)

    def _toggle_mark(self) -> None:
        question_id = self.session.questions[self.current_index].id
        if question_id in self.marked_ids:
            self.marked_ids.remove(question_id)
        else:
            self.marked_ids.add(question_id)
        self._refresh_card()

    def _refresh_card(self) -> None:
        answered = 0
        for index, (button, question) in enumerate(
            zip(self.number_buttons, self.session.questions, strict=True)
        ):
            is_answered = question.id in self.session.responses
            answered += int(is_answered)
            state = _answer_state(is_answered, self.checked_grades.get(question.id))
            button.setStyleSheet(
                _number_style(
                    state,
                    index == self.current_index,
                    question.id in self.marked_ids,
                )
            )
            button.setToolTip(_state_text(state))
        self.progress_label.setText(
            f"已答 {answered} / {len(self.session.questions)} · 已核对 {len(self.checked_grades)}"
        )

    def submit(self) -> None:
        unanswered = len(self.session.questions) - len(self.session.responses)
        message = "确认完成本次练习吗？"
        if unanswered:
            message = f"还有 {unanswered} 道题未作答，确认完成吗？"
        result = QMessageBox.question(
            self,
            "完成练习",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        grade = self.session.grade()
        self.service.save_progress(self.session.definition, grade)
        self.finished.emit(self.session, grade)

    def _exit(self) -> None:
        result = QMessageBox.question(
            self,
            "退出练习",
            "当前练习不会保存，确认退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.home_requested.emit()


class PracticeResultPage(QWidget):
    home_requested = Signal()

    def __init__(self, session: PracticeSession, grade: PracticeGrade, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 26, 34, 28)
        root.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel("练习结果")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        home = QPushButton("返回入口")
        home.clicked.connect(self.home_requested)
        header.addWidget(home)
        root.addLayout(header)
        scores = QHBoxLayout()
        scores.addWidget(_practice_score_panel("当前得分", f"{grade.strict_score} / {grade.max_score}"))
        if grade.estimated_score != grade.strict_score:
            scores.addWidget(_practice_score_panel("相似答案预估最高分", str(grade.estimated_score)))
        scores.addStretch(1)
        root.addLayout(scores)

        wrong_ids = set(grade.wrong_question_ids)
        if not wrong_ids:
            message = QLabel("本次练习全部答对。")
            message.setObjectName("entryTitle")
            root.addWidget(message)
            root.addStretch(1)
            return
        subtitle = QLabel(f"错题 {len(wrong_ids)} 道")
        subtitle.setObjectName("entryTitle")
        root.addWidget(subtitle)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        grades_by_id = {item.question_id: item for item in grade.questions}
        for index, question in enumerate(session.questions, start=1):
            if question.id not in wrong_ids:
                continue
            item = grades_by_id[question.id]
            layout.addWidget(
                ReviewQuestionPanel(
                    index,
                    question,
                    session.responses.get(question.id),
                    session.definition.assets,
                    strict_score=item.strict_score,
                    estimated_score=item.estimated_score,
                )
            )
        layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)


def _empty(response: object) -> bool:
    if response is None:
        return True
    if isinstance(response, str):
        return not response.strip()
    if isinstance(response, (list, tuple, set)):
        return not response or all(not str(value).strip() for value in response)
    return False


def _practice_score_panel(label: str, value: str) -> QFrame:
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


def _practice_feedback(question, response, grade: PracticeQuestionGrade) -> QFrame:
    panel = QFrame()
    panel.setObjectName("practiceFeedback")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(18, 15, 18, 17)
    layout.setSpacing(8)

    state = _answer_state(True, grade)
    title = QLabel(_state_text(state))
    title.setObjectName(
        {
            "correct": "feedbackCorrect",
            "partial": "feedbackPartial",
            "wrong": "feedbackWrong",
        }[state]
    )
    layout.addWidget(title)
    score = QLabel(f"本题得分：{grade.strict_score} / {grade.max_score} 分")
    score.setObjectName("entryTitle")
    layout.addWidget(score)
    if grade.estimated_score > grade.strict_score:
        estimate = QLabel(f"相似答案预估最高分：{grade.estimated_score} 分，最终以严格得分为准")
        estimate.setObjectName("warningText")
        estimate.setWordWrap(True)
        layout.addWidget(estimate)
    answer = QLabel("你的答案：" + format_response(question, response))
    answer.setWordWrap(True)
    layout.addWidget(answer)
    correct = QLabel("正确答案：" + correct_answer(question))
    correct.setWordWrap(True)
    layout.addWidget(correct)
    if question.basis:
        basis = QLabel("依据：" + question.basis)
        basis.setObjectName("pageMeta")
        basis.setWordWrap(True)
        layout.addWidget(basis)
    return panel


def _answer_state(answered: bool, grade: PracticeQuestionGrade | None) -> str:
    if grade is None:
        return "answered" if answered else "empty"
    if grade.strict_score == grade.max_score:
        return "correct"
    if grade.strict_score > 0 or grade.estimated_score > grade.strict_score:
        return "partial"
    return "wrong"


def _state_text(state: str) -> str:
    return {
        "empty": "未作答",
        "answered": "已作答，待核对",
        "correct": "回答正确",
        "partial": "部分得分或相似答案待复核",
        "wrong": "回答错误",
    }[state]


def _number_style(state: str, current: bool, marked: bool) -> str:
    palette = current_palette()
    background, color = {
        "empty": (palette.paper, palette.ink),
        "answered": (palette.accent, palette.accent_text),
        "correct": ("#2F8F5B", "#FFFFFF"),
        "partial": (palette.secondary, "#FFFFFF"),
        "wrong": (palette.error, "#FFFFFF"),
    }[state]
    border = palette.secondary if marked else (palette.accent if current else palette.line)
    width = 3 if current or marked else 1
    return (
        f"background:{background};color:{color};border:{width}px solid {border};"
        "border-radius:5px;padding:0;"
    )
