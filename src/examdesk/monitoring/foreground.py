from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ForegroundWindow:
    process_id: int | None
    process_name: str
    window_title: str
    event_kind: str = "window"

    @property
    def fingerprint(self) -> tuple[int | None, str, str, str]:
        return (self.process_id, self.process_name, self.window_title, self.event_kind)


@dataclass(frozen=True, slots=True)
class FocusEvent:
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    process_id: int | None
    application_name: str
    process_name: str
    window_title: str
    event_kind: str


class ForegroundProbe(Protocol):
    def current(self) -> ForegroundWindow: ...


class ForegroundEventTracker:
    def __init__(self, exam_process_id: int) -> None:
        self.exam_process_id = exam_process_id
        self._active_window: ForegroundWindow | None = None
        self._active_started_at: datetime | None = None
        self.events: list[FocusEvent] = []

    def observe(self, window: ForegroundWindow, at: datetime) -> bool:
        observed_at = at.astimezone(UTC)
        if window.process_id == self.exam_process_id:
            self._close_active(observed_at)
            return False
        if self._active_window is not None and self._active_window.fingerprint == window.fingerprint:
            return False
        self._close_active(observed_at)
        self._active_window = window
        self._active_started_at = observed_at
        return True

    def finalize(self, at: datetime) -> list[FocusEvent]:
        self._close_active(at.astimezone(UTC))
        return list(self.events)

    def _close_active(self, ended_at: datetime) -> None:
        if self._active_window is None or self._active_started_at is None:
            return
        duration = max(0.0, (ended_at - self._active_started_at).total_seconds())
        self.events.append(
            FocusEvent(
                started_at=self._active_started_at,
                ended_at=ended_at,
                duration_seconds=duration,
                process_id=self._active_window.process_id,
                application_name=_application_name(self._active_window.process_name),
                process_name=self._active_window.process_name,
                window_title=self._active_window.window_title,
                event_kind=self._active_window.event_kind,
            )
        )
        self._active_window = None
        self._active_started_at = None


class ForegroundMonitor:
    def __init__(
        self,
        tracker: ForegroundEventTracker,
        probe: ForegroundProbe,
        *,
        interval_seconds: float = 0.5,
        warning_callback: Callable[[ForegroundWindow], None] | None = None,
    ) -> None:
        self.tracker = tracker
        self.probe = probe
        self.interval_seconds = interval_seconds
        self.warning_callback = warning_callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="foreground-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> list[FocusEvent]:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return self.tracker.finalize(datetime.now(UTC))

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                window = self.probe.current()
                started = self.tracker.observe(window, datetime.now(UTC))
                if started and self.warning_callback is not None:
                    self.warning_callback(window)
            except Exception as exc:
                self.failure = str(exc)
                return


class WindowsForegroundProbe:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("foreground window monitoring is only available on Windows")
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32

    def current(self) -> ForegroundWindow:
        handle = self.user32.GetForegroundWindow()
        if not handle:
            return ForegroundWindow(None, "未知程序", "", "unknown")
        title_length = self.user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(max(title_length + 1, 2))
        self.user32.GetWindowTextW(handle, title_buffer, len(title_buffer))
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        process_name = self._process_name(process_id.value)
        event_kind = _event_kind(process_name)
        return ForegroundWindow(process_id.value or None, process_name, title_buffer.value, event_kind)

    def _process_name(self, process_id: int) -> str:
        if not process_id:
            return "未知程序"
        process_handle = self.kernel32.OpenProcess(
            self.PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            process_id,
        )
        if not process_handle:
            return f"PID {process_id}"
        try:
            size = wintypes.DWORD(32768)
            path_buffer = ctypes.create_unicode_buffer(size.value)
            if self.kernel32.QueryFullProcessImageNameW(
                process_handle,
                0,
                path_buffer,
                ctypes.byref(size),
            ):
                return Path(path_buffer.value).name or f"PID {process_id}"
            return f"PID {process_id}"
        finally:
            self.kernel32.CloseHandle(process_handle)


def _application_name(process_name: str) -> str:
    return Path(process_name).stem or process_name or "未知程序"


def _event_kind(process_name: str) -> str:
    normalized = process_name.casefold()
    if normalized == "logonui.exe":
        return "lock_screen"
    if normalized == "taskmgr.exe":
        return "task_manager"
    return "window"

