from pathlib import Path

from examdesk.paths import AppPaths, safe_file_part


def test_app_paths_create_expected_directories(tmp_path: Path) -> None:
    paths = AppPaths.from_root(tmp_path / "runtime")
    paths.ensure()

    assert paths.database == (tmp_path / "runtime" / "data.sqlite3").resolve()
    assert paths.state.is_dir()
    assert paths.results.is_dir()
    assert paths.practice.is_dir()
    assert paths.assets.is_dir()
    assert paths.logs.is_dir()
    assert paths.updates.is_dir()


def test_safe_file_part_removes_windows_invalid_characters() -> None:
    assert safe_file_part('  场次:一/测试用户甲*  ') == "场次_一_测试用户甲_"
    assert safe_file_part("   ") == "未命名"
