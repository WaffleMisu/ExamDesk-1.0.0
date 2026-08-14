from pathlib import Path

import pytest

from examdesk.db.admin_repository import AdminRepository
from examdesk.db.connection import Database
from examdesk.db.migrations import initialize_database
from examdesk.domain.enums import AdminRole
from examdesk.security.passwords import hash_secret


def make_repository(tmp_path: Path) -> AdminRepository:
    database_path = tmp_path / "admin.sqlite3"
    initialize_database(database_path)
    return AdminRepository(Database(database_path))


def test_first_admin_is_supervisor_and_can_authenticate(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    recovery_digest = hash_secret("RECOVERY").encode()

    administrator = repository.create_first_admin("测试用户甲", "admin-pass", recovery_digest)

    assert administrator.role is AdminRole.SUPERVISOR
    assert repository.authenticate("测试用户甲", "admin-pass") == administrator
    assert repository.authenticate("测试用户甲", "wrong") is None


def test_only_supervisor_can_add_up_to_two_admins(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    supervisor = repository.create_first_admin(
        "主管理员",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    first = repository.add_admin(supervisor.id, "管理员一", "first-pass")
    repository.add_admin(supervisor.id, "管理员二", "second-pass")

    with pytest.raises(ValueError, match="limit"):
        repository.add_admin(supervisor.id, "管理员三", "third-pass")

    with pytest.raises(PermissionError):
        repository.add_admin(first.id, "越权账户", "password")

    with pytest.raises(ValueError, match="已经存在"):
        repository.add_admin(supervisor.id, "管理员一", "duplicate-pass")

    assert len(repository.list_all()) == 3


def test_recovery_code_resets_supervisor_password(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    recovery_code = "RECOVERY-CODE"
    repository.create_first_admin(
        "主管理员",
        "old-password",
        hash_secret(recovery_code).encode(),
    )

    repository.reset_supervisor_password(recovery_code, "new-password")

    assert repository.authenticate("主管理员", "old-password") is None
    assert repository.authenticate("主管理员", "new-password") is not None
    with pytest.raises(PermissionError):
        repository.reset_supervisor_password("WRONG", "another-password")


def test_supervisor_can_rotate_recovery_code_and_old_code_becomes_invalid(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    old_code = "OLD-RECOVERY-CODE"
    supervisor = repository.create_first_admin(
        "Supervisor",
        "supervisor-pass",
        hash_secret(old_code).encode(),
    )

    new_code = repository.rotate_supervisor_recovery_code(
        supervisor.id,
        "supervisor-pass",
    )

    assert new_code != old_code
    with pytest.raises(PermissionError):
        repository.reset_supervisor_password(old_code, "old-code-password")
    repository.reset_supervisor_password(new_code, "new-password")
    assert repository.authenticate("Supervisor", "new-password") is not None
    with repository.database.connect() as connection:
        audit = connection.execute(
            """
            SELECT details_json FROM audit_events
            WHERE action = 'rotate_supervisor_recovery_code'
            """
        ).fetchone()
    assert audit is not None
    assert new_code not in audit["details_json"]


def test_recovery_code_rotation_requires_current_supervisor_password(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    supervisor = repository.create_first_admin(
        "Supervisor",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    administrator = repository.add_admin(supervisor.id, "Worker", "worker-pass")

    with pytest.raises(PermissionError):
        repository.rotate_supervisor_recovery_code(supervisor.id, "wrong-password")
    with pytest.raises(PermissionError):
        repository.rotate_supervisor_recovery_code(administrator.id, "worker-pass")

    repository.reset_supervisor_password("RECOVERY", "still-valid")
    assert repository.authenticate("Supervisor", "still-valid") is not None


def test_supervisor_can_deactivate_admin_and_revoke_work_authorization(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    supervisor = repository.create_first_admin(
        "Supervisor",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    administrator = repository.add_admin(supervisor.id, "Worker", "temporary-pass")
    with repository.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO admin_work_authorizations(
                id, admin_id, bank_id, patch_secret, base_revision,
                auth_generation, issued_at
            ) VALUES ('work-1', ?, 'bank-1', ?, 0, ?, '2026-08-13T00:00:00+00:00')
            """,
            (administrator.id, b"secret", administrator.auth_generation),
        )

    repository.deactivate_admin(
        supervisor.id,
        administrator.id,
        supervisor_password="supervisor-pass",
        reason="No longer responsible for the bank",
    )

    removed = repository.get(administrator.id)
    assert removed.is_active is False
    assert removed.auth_generation == 2
    assert repository.authenticate("Worker", "temporary-pass") is None
    with repository.database.connect() as connection:
        authorization = connection.execute(
            "SELECT revoked_at FROM admin_work_authorizations WHERE id = 'work-1'"
        ).fetchone()
        audit = connection.execute(
            """
            SELECT details_json FROM audit_events
            WHERE action = 'deactivate_admin' AND entity_id = ?
            """,
            (administrator.id,),
        ).fetchone()
    assert authorization["revoked_at"] is not None
    assert "No longer responsible" in audit["details_json"]


def test_deactivated_admin_slot_can_be_reused_without_restoring_old_authorization(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    supervisor = repository.create_first_admin(
        "Supervisor",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    first = repository.add_admin(supervisor.id, "Worker", "temporary-pass")
    repository.add_admin(supervisor.id, "Other", "other-pass")
    repository.deactivate_admin(
        supervisor.id,
        first.id,
        supervisor_password="supervisor-pass",
        reason="Role changed",
    )

    restored = repository.add_admin(supervisor.id, "Worker", "new-temporary-pass")

    assert restored.id == first.id
    assert restored.is_active is True
    assert restored.auth_generation == 3
    assert repository.authenticate("Worker", "new-temporary-pass") == restored
    assert len([item for item in repository.list_all() if item.is_active]) == 3


def test_admin_deactivation_requires_supervisor_password_and_rejects_supervisor_target(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    supervisor = repository.create_first_admin(
        "Supervisor",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    administrator = repository.add_admin(supervisor.id, "Worker", "temporary-pass")

    with pytest.raises(PermissionError, match="密码不正确"):
        repository.deactivate_admin(
            supervisor.id,
            administrator.id,
            supervisor_password="wrong-password",
            reason="Test",
        )
    with pytest.raises(PermissionError, match="不能被移除"):
        repository.deactivate_admin(
            supervisor.id,
            supervisor.id,
            supervisor_password="supervisor-pass",
            reason="Test",
        )

    assert repository.get(administrator.id).is_active is True
    assert repository.get(supervisor.id).is_active is True
