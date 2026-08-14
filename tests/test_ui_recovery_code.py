from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from examdesk.db.admin_repository import AdminRepository
from examdesk.db.connection import Database
from examdesk.db.migrations import initialize_database
from examdesk.security.passwords import hash_secret
from examdesk.ui import system_maintenance
from examdesk.ui.system_maintenance import (
    RecoveryCodeDisplayDialog,
    RotateRecoveryCodeDialog,
)


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def make_repository(tmp_path: Path) -> AdminRepository:
    database_path = tmp_path / "recovery-ui.sqlite3"
    initialize_database(database_path)
    return AdminRepository(Database(database_path))


def test_rotation_dialog_reauthenticates_and_rotates_code(
    tmp_path: Path,
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_repository(tmp_path)
    old_code = "OLD-RECOVERY"
    supervisor = repository.create_first_admin(
        "Supervisor",
        "supervisor-pass",
        hash_secret(old_code).encode(),
    )
    shown_codes: list[str] = []

    def show_code(dialog: RecoveryCodeDisplayDialog) -> QDialog.DialogCode:
        shown_codes.append(dialog.recovery_code)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(system_maintenance.RecoveryCodeDisplayDialog, "exec", show_code)
    dialog = RotateRecoveryCodeDialog(repository, supervisor.id)
    dialog.password_edit.setText("supervisor-pass")

    dialog._rotate()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(shown_codes) == 1
    with pytest.raises(PermissionError):
        repository.reset_supervisor_password(old_code, "wrong-new-password")
    repository.reset_supervisor_password(shown_codes[0], "new-password")
    assert repository.authenticate("Supervisor", "new-password") is not None


def test_recovery_code_display_can_copy_code(qt_application: QApplication) -> None:
    dialog = RecoveryCodeDisplayDialog("ABCD-EFGH")

    dialog._copy()

    assert QApplication.clipboard().text() == "ABCD-EFGH"

