from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox

from examdesk.ui.practice_runner import PracticeRunnerPage


class EqualYes:
    def __eq__(self, other: object) -> bool:
        return other == QMessageBox.StandardButton.Yes


class SignalRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def emit(self, *args: object) -> None:
        self.calls.append(args)


def test_submit_accepts_an_equal_yes_button_value(monkeypatch) -> None:
    answer = EqualYes()
    assert answer == QMessageBox.StandardButton.Yes
    assert answer is not QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", lambda *args: answer)

    grade = object()
    definition = object()
    session = SimpleNamespace(
        questions=[object()],
        responses={"answered": object()},
        definition=definition,
        grade=lambda: grade,
    )
    saved: list[tuple[object, object]] = []
    service = SimpleNamespace(save_progress=lambda current, result: saved.append((current, result)))
    finished = SignalRecorder()
    runner = SimpleNamespace(session=session, service=service, finished=finished)

    PracticeRunnerPage.submit(runner)

    assert saved == [(definition, grade)]
    assert finished.calls == [(session, grade)]


def test_exit_accepts_an_equal_yes_button_value(monkeypatch) -> None:
    answer = EqualYes()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: answer)
    home_requested = SignalRecorder()
    runner = SimpleNamespace(home_requested=home_requested)

    PracticeRunnerPage._exit(runner)

    assert home_requested.calls == [()]
