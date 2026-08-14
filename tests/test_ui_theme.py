from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from examdesk.ui.background import BackgroundSurface, _cover_source_rect
from examdesk.ui.theme import THEMES, ThemeManager
from examdesk.ui.theme_dialog import ThemeDialog
from examdesk.ui.theme_settings import ThemeSettings, ThemeSettingsStore


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_theme_manager_uses_blue_default_and_persists_local_choice(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    store = ThemeSettingsStore(tmp_path / "app")
    manager = ThemeManager(qt_application, store)

    assert manager.state.palette.id == "clean_blue"
    assert "#2563A6" in qt_application.styleSheet()

    manager.commit(ThemeSettings(theme_id="classic_green", accent_color="#1144AA"))
    restored = ThemeManager(qt_application, store)

    assert restored.settings.theme_id == "classic_green"
    assert restored.state.palette.accent == "#1144AA"
    assert "#1144AA" in qt_application.styleSheet()


def test_all_named_themes_are_available() -> None:
    assert {theme.name for theme in THEMES.values()} == {
        "清爽蓝",
        "经典绿",
        "极简浅色",
        "珊瑚红",
        "石墨深色",
        "明亮青绿",
    }


def test_closing_theme_dialog_restores_preview(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    manager = ThemeManager(qt_application, ThemeSettingsStore(tmp_path / "app"))
    dialog = ThemeDialog(manager)
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("classic_green"))
    qt_application.processEvents()

    assert manager.state.palette.id == "classic_green"

    dialog.reject()

    assert manager.state.palette.id == "clean_blue"


def test_background_surface_scope_changes_with_page(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    store = ThemeSettingsStore(tmp_path / "app")
    manager = ThemeManager(qt_application, store)
    manager.state = manager.preview(
        ThemeSettings(
            theme_id="clean_blue",
            background_scope="home",
            background_file="preview.png",
        ),
        tmp_path / "preview.png",
    )
    surface = BackgroundSurface(manager)

    assert surface._should_draw_image()
    surface.set_home_active(False)
    assert not surface._should_draw_image()


def test_cover_source_rect_crops_without_stretching() -> None:
    wide = _cover_source_rect(1600, 900, 800, 800)
    tall = _cover_source_rect(900, 1600, 1200, 600)

    assert round(wide.width()) == 900
    assert round(wide.height()) == 900
    assert round(tall.width()) == 900
    assert round(tall.height()) == 450
