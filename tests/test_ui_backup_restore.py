from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.maintenance import BackupService
from examdesk.security import OrganizationKeyStore
from examdesk.security.passwords import hash_secret
from examdesk.ui.admin_auth import FirstAdminDialog
from examdesk.ui.system_maintenance import BackupRestoreDialog


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_first_initialization_offers_backup_restore(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    database_path = tmp_path / "empty.sqlite3"
    initialize_database(database_path)
    dialog = FirstAdminDialog(AdminRepository(Database(database_path)))

    restore = dialog.findChild(QPushButton, "restoreBackupButton")

    assert restore is not None
    restore.click()
    assert dialog.restore_requested is True


def test_fresh_install_can_restore_with_backup_password_and_certificate(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    source_database_path = tmp_path / "source" / "data.sqlite3"
    initialize_database(source_database_path)
    source_database = Database(source_database_path)
    source_admins = AdminRepository(source_database)
    source_admins.create_first_admin(
        "测试主管理员",
        "supervisor-pass",
        hash_secret("RECOVERY-CODE").encode(),
    )
    source_keys = OrganizationKeyStore(source_database)
    source_keys.ensure_initialized()
    backup_path = tmp_path / "portable.exambackup"
    BackupService(source_database, tmp_path / "source" / "assets").create(
        backup_path,
        password="backup-pass",
        key_store=source_keys,
        software_version="1.0.0",
    )
    certificate_path = tmp_path / "source.examtrust"
    certificate_path.write_bytes(source_keys.export_trust_certificate())

    target_database_path = tmp_path / "target" / "data.sqlite3"
    initialize_database(target_database_path)
    target_database = Database(target_database_path)
    dialog = BackupRestoreDialog(
        target_database,
        tmp_path / "target" / "assets",
        OrganizationKeyStore(target_database),
    )
    dialog.backup_edit.setText(str(backup_path))
    dialog.backup_password.setText("backup-pass")
    dialog.certificate_edit.setText(str(certificate_path))
    dialog.confirm_edit.setText("RESTORE")

    dialog._restore()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.restored is not None
    assert AdminRepository(Database(target_database_path)).authenticate(
        "测试主管理员",
        "supervisor-pass",
    ) is not None
    OrganizationKeyStore(Database(target_database_path)).load()
