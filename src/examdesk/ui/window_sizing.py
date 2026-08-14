from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget


def bounded_window_size(
    available: QSize,
    preferred: QSize,
    minimum: QSize,
    *,
    margin: int = 24,
) -> tuple[QSize, QSize]:
    max_width = max(320, available.width() - margin * 2)
    max_height = max(320, available.height() - margin * 2)
    target = QSize(
        min(preferred.width(), max_width),
        min(preferred.height(), max_height),
    )
    bounded_minimum = QSize(
        min(minimum.width(), target.width()),
        min(minimum.height(), target.height()),
    )
    return target, bounded_minimum


def fit_window_to_available(
    window: QWidget,
    preferred_width: int,
    preferred_height: int,
    *,
    minimum_width: int = 640,
    minimum_height: int = 420,
    margin: int = 24,
) -> None:
    parent = window.parentWidget()
    screen = parent.screen() if parent is not None else window.screen()
    if screen is None:
        application = QGuiApplication.instance()
        screen = application.primaryScreen() if application is not None else None
    if screen is None:
        window.setMinimumSize(minimum_width, minimum_height)
        window.resize(preferred_width, preferred_height)
        return
    target, bounded_minimum = bounded_window_size(
        screen.availableGeometry().size(),
        QSize(preferred_width, preferred_height),
        QSize(minimum_width, minimum_height),
        margin=margin,
    )
    window.setMinimumSize(bounded_minimum)
    window.resize(target)
