from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from examdesk.paths import AppPaths
from examdesk.ui import ApplicationContext, MainWindow


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_main_window_has_three_real_entry_points(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    context = ApplicationContext.create(AppPaths.from_root(tmp_path / "app-data"))
    window = MainWindow(context)
    window.show()
    qt_application.processEvents()

    assert window.windowTitle().startswith("ExamDesk 离线考试系统 1.0.0")
    assert window.windowTitle().endswith("主管理员版")
    assert window.home_page.findChild(object, "adminEntry") is not None
    assert window.home_page.findChild(object, "examEntry") is not None
    assert window.home_page.findChild(object, "practiceEntry") is not None
    assert window.home_page.findChild(object, "themeButton") is not None
    assert window.home_page.findChild(object, "trustButton") is None
    assert window.theme_manager.settings.theme_id == "clean_blue"
    assert context.paths.database.is_file()
    window.close()


def test_candidate_window_has_no_administrator_entry_or_initialization(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    context = ApplicationContext.create(AppPaths.from_root(tmp_path / "candidate-data"))
    window = MainWindow(context, admin_enabled=False)
    window.show()
    qt_application.processEvents()

    assert window.windowTitle().endswith("考生协作版")
    assert window.home_page.findChild(object, "adminEntry") is None
    assert window.home_page.findChild(object, "examEntry") is not None
    assert window.home_page.findChild(object, "practiceEntry") is not None
    assert window.home_page.findChild(object, "collaborationEntry") is not None
    trust_button = window.home_page.findChild(object, "trustButton")
    assert trust_button is not None
    assert trust_button.text() == "信任证书（0）"
    window.open_admin()
    assert context.administrators.list_all() == []
    collaboration_root = context.paths.root / "collaboration"
    assert collaboration_root.is_dir()
    assert not (collaboration_root / "data.sqlite3").samefile(context.paths.database)
    window.close()


def test_candidate_can_batch_import_multiple_trust_certificates(
    tmp_path: Path,
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_master = ApplicationContext.create(AppPaths.from_root(tmp_path / "first-master"))
    second_master = ApplicationContext.create(AppPaths.from_root(tmp_path / "second-master"))
    first_master.organization_keys.ensure_initialized()
    second_master.organization_keys.ensure_initialized()
    first_path = tmp_path / "first.examtrust"
    second_path = tmp_path / "second.examtrust"
    first_path.write_bytes(first_master.organization_keys.export_trust_certificate())
    second_path.write_bytes(second_master.organization_keys.export_trust_certificate())

    context = ApplicationContext.create(AppPaths.from_root(tmp_path / "candidate-trust-data"))
    window = MainWindow(context, admin_enabled=False)
    messages: list[str] = []
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda *args, **kwargs: ([str(first_path), str(second_path), str(first_path)], ""),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: messages.append(message),
    )

    window.import_trust_certificates()

    assert len(context.organization_keys.trusted_public_keys()) == 2
    assert window.home_page.findChild(object, "trustButton").text() == "信任证书（2）"
    assert "新增：2 个" in messages[0]
    assert "已存在：1 个" in messages[0]
    assert "当前可信证书总数：2 个" in messages[0]
    window.close()
