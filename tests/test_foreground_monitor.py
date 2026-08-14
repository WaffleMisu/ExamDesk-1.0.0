import os
from datetime import UTC, datetime, timedelta

from examdesk.monitoring import ForegroundEventTracker, ForegroundWindow, WindowsForegroundProbe


def test_tracker_merges_same_window_and_splits_different_external_windows() -> None:
    tracker = ForegroundEventTracker(exam_process_id=100)
    start = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    exam = ForegroundWindow(100, "exam.exe", "考试")
    word = ForegroundWindow(200, "WINWORD.EXE", "参考资料.docx")
    chat = ForegroundWindow(300, "通讯工具.exe", "文件传输")

    assert not tracker.observe(exam, start)
    assert tracker.observe(word, start + timedelta(seconds=1))
    assert not tracker.observe(word, start + timedelta(seconds=3))
    assert tracker.observe(chat, start + timedelta(seconds=6))
    assert not tracker.observe(exam, start + timedelta(seconds=8))

    assert len(tracker.events) == 2
    assert tracker.events[0].application_name == "WINWORD"
    assert tracker.events[0].window_title == "参考资料.docx"
    assert tracker.events[0].duration_seconds == 5
    assert tracker.events[1].process_name == "通讯工具.exe"
    assert tracker.events[1].duration_seconds == 2


def test_tracker_records_lock_screen_kind() -> None:
    tracker = ForegroundEventTracker(exam_process_id=100)
    start = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    tracker.observe(ForegroundWindow(500, "LogonUI.exe", "", "lock_screen"), start)
    events = tracker.finalize(start + timedelta(seconds=4))
    assert events[0].event_kind == "lock_screen"


def test_windows_probe_returns_current_process_information() -> None:
    if os.name != "nt":
        return
    window = WindowsForegroundProbe().current()
    assert window.process_name
    assert window.event_kind in {"window", "lock_screen", "task_manager", "unknown"}

