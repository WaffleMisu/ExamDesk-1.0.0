from PySide6.QtCore import QSize

from examdesk.ui.window_sizing import bounded_window_size


def test_large_window_is_bounded_to_1024_by_600_work_area() -> None:
    target, minimum = bounded_window_size(
        QSize(1024, 600),
        QSize(1280, 780),
        QSize(800, 520),
        margin=12,
    )

    assert target == QSize(1000, 576)
    assert minimum == QSize(800, 520)


def test_dialog_minimum_never_exceeds_its_small_screen_target() -> None:
    target, minimum = bounded_window_size(
        QSize(800, 500),
        QSize(1040, 680),
        QSize(700, 440),
    )

    assert target == QSize(752, 452)
    assert minimum == QSize(700, 440)
