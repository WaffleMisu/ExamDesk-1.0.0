from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.domain.enums import AdminRole
from examdesk.paths import AppPaths
from examdesk.security.passwords import hash_secret
from examdesk.ui import ApplicationContext, MainWindow
from examdesk.ui.admin_auth import AdminLoginDialog
from examdesk.ui.admin_workspace import AdminWorkspace
from examdesk.ui.collaboration_ui import InstallWorkPackageDialog


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_admin_login_dialog_can_require_supervisor(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    path = tmp_path / "data.sqlite3"
    initialize_database(path)
    admins = AdminRepository(Database(path))
    supervisor = admins.create_first_admin(
        "主管理员",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    admins.add_admin(supervisor.id, "协作员", "ordinary-pass")
    dialog = AdminLoginDialog(admins, required_role=AdminRole.SUPERVISOR)
    dialog.name_edit.setText("协作员")
    dialog.password_edit.setText("ordinary-pass")

    dialog._authenticate()

    assert dialog.administrator is None
    assert dialog.error_label.text() == "姓名或密码不正确"


def test_work_package_password_and_local_login_password_are_independent(
    qt_application: QApplication,
) -> None:
    dialog = InstallWorkPackageDialog(Path("demo.bankwork"))
    dialog.first_edit.setText("package-pass")
    dialog.second_edit.setText("local-login-pass")
    dialog.confirm_edit.setText("local-login-pass")

    dialog.accept_values()

    assert dialog.result() == dialog.DialogCode.Accepted


def test_supervisor_login_opens_admin_workspace(
    tmp_path: Path,
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ApplicationContext.create(AppPaths.from_root(tmp_path / "admin-data"))
    supervisor = context.administrators.create_first_admin(
        "主管理员",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    monkeypatch.setattr(
        AdminLoginDialog,
        "exec",
        lambda dialog: (
            setattr(dialog, "administrator", supervisor)
            or QDialog.DialogCode.Accepted
        ),
    )
    window = MainWindow(context, admin_enabled=True)

    window.open_admin()

    assert isinstance(window.pages.currentWidget(), AdminWorkspace)
    assert window.pages.count() == 2
    window.close()
