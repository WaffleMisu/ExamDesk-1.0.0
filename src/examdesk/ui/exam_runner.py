from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QCloseEvent
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

from examdesk.domain.enums import SubmitReason
from examdesk.exam import ExamState, ExamTimer
from examdesk.monitoring import (
    ForegroundEventTracker,
    ForegroundMonitor,
    ForegroundWindow,
    WindowsForegroundProbe,
)
from examdesk.results import AttemptService
from examdesk.sessions import ExamDefinition

from .question_view import QuestionView
from .theme import current_palette


class ExamRunnerPage(QWidget):
    submitted = Signal(object)
    focus_warning = Signal(object)

    def __init__(
        self,
        definition: ExamDefinition,
        state: ExamState,
        attempts: AttemptService,
        local_result_directory: Path,
        submission_directory: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.state = state
        self.attempts = attempts
        self.local_result_directory = local_result_directory
        self.submission_directory = submission_directory
        self.timer = ExamTimer(state)
        self.number_buttons: list[QPushButton] = []
        self.monitor = self._create_monitor() if definition.monitoring_enabled else None
        self.focus_warning.connect(self._handle_focus_warning)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top_bar())
        self.warning_label = QLabel()
        self.warning_label.setObjectName("examWarning")
        self.warning_label.setVisible(False)
        root.addWidget(self.warning_label)

        body = QHBoxLayout()
        body.setContentsMargins(24, 20, 0, 20)
        body.setSpacing(20)
        body.addWidget(self._question_area(), 1)
        body.addWidget(self._answer_card())
        root.addLayout(body, 1)

        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self._tick)
        self.tick_timer.start()
        self.checkpoint_timer = QTimer(self)
        self.checkpoint_timer.setInterval(15_000)
        self.checkpoint_timer.timeout.connect(self._checkpoint)
        self.checkpoint_timer.start()
        if self.monitor is not None:
            self.monitor.start()
        self.show_question()
        self._tick()

    def _create_monitor(self) -> ForegroundMonitor:
        probe = WindowsForegroundProbe()
        probe.current()
        tracker = ForegroundEventTracker(os.getpid())
        return ForegroundMonitor(
            tracker,
            probe,
            warning_callback=lambda window: self.focus_warning.emit(window),
        )

    def _top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("examTopBar")
        bar.setFixedHeight(68)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        title = QLabel(self.definition.name)
        title.setObjectName("entryTitle")
        layout.addWidget(title)
        candidate = QLabel(self.state.candidate_name)
        candidate.setObjectName("pageMeta")
        layout.addWidget(candidate)
        layout.addStretch(1)
        self.timer_label = QLabel()
        self.timer_label.setObjectName("timerLabel")
        layout.addWidget(self.timer_label)
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
        self.previous_button.clicked.connect(self.previous_question)
        nav.addWidget(self.previous_button)
        nav.addStretch(1)
        self.next_button = QPushButton("下一题")
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(self.next_question)
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
        numbers = QWidget()
        grid = QGridLayout(numbers)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setSpacing(8)
        for index in range(len(self.state.displayed_questions)):
            button = QPushButton(str(index + 1))
            button.setObjectName("numberButton")
            button.clicked.connect(lambda checked=False, value=index + 1: self.jump_to(value))
            grid.addWidget(button, index // 5, index % 5)
            self.number_buttons.append(button)
        grid.setRowStretch((len(self.number_buttons) + 4) // 5, 1)
        scroll.setWidget(numbers)
        layout.addWidget(scroll, 1)

        self.submit_button = QPushButton("提交试卷")
        self.submit_button.clicked.connect(lambda: self.submit_exam(SubmitReason.MANUAL))
        layout.addWidget(self.submit_button)
        return card

    def show_question(self) -> None:
        displayed = self.state.displayed_questions[self.state.current_index]
        session_question = self.state.question_at(self.definition)
        question = session_question.question
        view = QuestionView(
            question,
            displayed.option_order,
            self.state.responses.get(displayed.question_id),
            self.definition.assets,
        )
        view.response_changed.connect(
            lambda response, question_id=displayed.question_id: self._set_response(question_id, response)
        )
        old = self.question_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.question_scroll.setWidget(view)
        self.question_scroll.verticalScrollBar().setValue(0)
        self.position_label.setText(
            f"第 {self.state.current_index + 1} 题 / 共 {len(self.state.displayed_questions)} 题"
        )
        self.mark_button.blockSignals(True)
        self.mark_button.setChecked(displayed.question_id in self.state.marked_question_ids)
        self.mark_button.blockSignals(False)
        self.previous_button.setEnabled(self.state.current_index > 0)
        self.next_button.setEnabled(self.state.current_index + 1 < len(self.state.displayed_questions))
        self._refresh_answer_card()

    def previous_question(self) -> None:
        self._checkpoint()
        self.state.go_previous()
        self.show_question()

    def next_question(self) -> None:
        self._checkpoint()
        self.state.go_next()
        self.show_question()

    def jump_to(self, display_number: int) -> None:
        self._checkpoint()
        self.state.jump_to(display_number)
        self.show_question()

    def _set_response(self, question_id: str, response: object) -> None:
        self.state.set_response(question_id, response)
        self._checkpoint()
        self._refresh_answer_card()

    def _toggle_mark(self) -> None:
        question_id = self.state.displayed_questions[self.state.current_index].question_id
        self.state.toggle_mark(question_id)
        self._checkpoint()
        self._refresh_answer_card()

    def _checkpoint(self) -> None:
        self.timer.checkpoint()
        try:
            self.attempts.checkpoint(self.state)
        except OSError as exc:
            self.warning_label.setText(f"自动保存失败：{exc}")
            self.warning_label.setVisible(True)

    def _tick(self) -> None:
        remaining = self.timer.remaining_seconds()
        self.timer_label.setText(_format_time(remaining))
        warnings = self.timer.due_warnings()
        if warnings:
            self.warning_label.setText(f"距离自动交卷还有 {_format_time(min(warnings))}")
            self.warning_label.setVisible(True)
        if self.timer.is_expired():
            self.submit_exam(SubmitReason.TIMEOUT)

    def _refresh_answer_card(self) -> None:
        answered = 0
        for index, (button, displayed) in enumerate(
            zip(self.number_buttons, self.state.displayed_questions, strict=True)
        ):
            is_answered = displayed.question_id in self.state.responses
            is_current = index == self.state.current_index
            is_marked = displayed.question_id in self.state.marked_question_ids
            answered += int(is_answered)
            button.setStyleSheet(_number_style(is_answered, is_current, is_marked))
        self.progress_label.setText(f"已答 {answered} / {len(self.number_buttons)}")

    def _handle_focus_warning(self, window: ForegroundWindow) -> None:
        title = window.window_title or window.process_name
        self.warning_label.setText(f"已记录切出考试窗口：{title}")
        self.warning_label.setVisible(True)
        top = self.window()
        top.showNormal() if top.isMinimized() else None
        top.raise_()
        top.activateWindow()

    def submit_exam(self, reason: SubmitReason) -> None:
        if not self.submit_button.isEnabled():
            return
        unanswered = len(self.state.unanswered_question_ids())
        if reason is SubmitReason.MANUAL:
            message = "确认提交试卷吗？"
            if unanswered:
                message = f"还有 {unanswered} 道题未作答，确认提交试卷吗？"
            result = QMessageBox.question(
                self,
                "提交试卷",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return
        self.submit_button.setEnabled(False)
        self.tick_timer.stop()
        self.checkpoint_timer.stop()
        events = self.monitor.stop() if self.monitor is not None else []
        monitor_status = "disabled"
        if self.monitor is not None:
            monitor_status = (
                "ok" if self.monitor.failure is None else f"failed:{self.monitor.failure}"
            )
        try:
            self.attempts.finalize(
                self.definition,
                self.state,
                reason=reason,
                foreground_events=events,
                monitor_status=monitor_status,
                local_result_directory=self.local_result_directory,
                submission_directory=self.submission_directory,
                submitted_at=datetime.now(UTC),
            )
            review = self.attempts.review_store.load_latest(
                self.definition,
                self.state.candidate_name,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "交卷失败", str(exc))
            self.submit_button.setEnabled(True)
            self.tick_timer.start()
            self.checkpoint_timer.start()
            if self.monitor is not None:
                self.monitor.start()
            return
        self.submitted.emit(review)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.state.status.value == "active":
            QMessageBox.warning(self, "考试进行中", "考试进行中，不能关闭答题窗口。")
            event.ignore()
            return
        event.accept()


def _format_time(seconds: int | None) -> str:
    if seconds is None:
        return "不限时"
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _number_style(answered: bool, current: bool, marked: bool) -> str:
    palette = current_palette()
    background = palette.accent if answered else palette.paper
    color = palette.accent_text if answered else palette.ink
    border = palette.secondary if marked else (palette.accent if current else palette.line)
    width = 3 if current or marked else 1
    return (
        f"background:{background};color:{color};border:{width}px solid {border};"
        "border-radius:5px;padding:0;"
    )
