from __future__ import annotations

from pathlib import Path

from PIL import Image

from examdesk.ui.theme_settings import ThemeSettings, ThemeSettingsStore


def test_theme_settings_default_to_clean_blue_and_round_trip(tmp_path: Path) -> None:
    store = ThemeSettingsStore(tmp_path / "app")

    assert store.load().theme_id == "clean_blue"

    saved = store.save(
        ThemeSettings(
            theme_id="classic_green",
            accent_color="#123abc",
        )
    )

    assert saved.accent_color == "#123ABC"
    assert store.load() == saved


def test_theme_settings_fall_back_when_json_is_damaged(tmp_path: Path) -> None:
    store = ThemeSettingsStore(tmp_path / "app")
    store.app_directory.mkdir(parents=True)
    store.settings_path.write_text("{broken", encoding="utf-8")

    settings = store.load()

    assert settings == ThemeSettings()


def test_theme_background_is_copied_and_survives_source_removal(tmp_path: Path) -> None:
    source = tmp_path / "背景.png"
    Image.new("RGB", (1600, 900), (170, 35, 55)).save(source, format="PNG")
    store = ThemeSettingsStore(tmp_path / "app")

    installed_name = store.install_background(source)
    settings = store.save(
        ThemeSettings(
            theme_id="clean_blue",
            background_scope="all",
            background_file=installed_name,
        )
    )
    source.unlink()

    assert store.background_path(settings).is_file()
    assert store.load().background_scope == "all"


def test_theme_settings_reject_unknown_values_and_missing_background(tmp_path: Path) -> None:
    store = ThemeSettingsStore(tmp_path / "app")
    store.save(
        ThemeSettings(
            theme_id="unknown",
            accent_color="red",
            background_scope="all",
            background_file="missing.png",
        )
    )

    loaded = store.load()

    assert loaded.theme_id == "clean_blue"
    assert loaded.accent_color is None
    assert loaded.background_scope == "none"
    assert loaded.background_file == ""
