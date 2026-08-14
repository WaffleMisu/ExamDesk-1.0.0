from pathlib import Path

import pytest

from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.maintenance import FactoryResetService
from examdesk.security.passwords import hash_secret


def test_factory_reset_requires_supervisor_and_stages_whole_data_directory(tmp_path: Path) -> None:
    root = tmp_path / "ExamDesk"
    database_path = root / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    admins = AdminRepository(database)
    supervisor = admins.create_first_admin("主管理员", "supervisor-pass", hash_secret("RECOVERY").encode())
    admins.add_admin(supervisor.id, "管理员", "admin-pass")
    (root / "assets").mkdir()
    (root / "assets" / "sample.bin").write_bytes(b"data")

    service = FactoryResetService(database, root)
    preview = service.preview()
    assert (preview.administrators, preview.assets) == (2, 0)

    with pytest.raises(PermissionError):
        service.authenticate_and_record("not-supervisor", "wrong", skipped_backup=True)

    service.authenticate_and_record(supervisor.id, "supervisor-pass", skipped_backup=True)
    staged = service.stage_reset()

    assert not (root / "data.sqlite3").exists()
    assert (staged / "data.sqlite3").exists()
    assert (staged / "assets" / "sample.bin").read_bytes() == b"data"
    root.rmdir()
    import shutil

    shutil.rmtree(staged)
