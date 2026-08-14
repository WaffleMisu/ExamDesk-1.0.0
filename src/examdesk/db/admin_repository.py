from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from examdesk.domain.enums import AdminRole
from examdesk.security.passwords import generate_recovery_code, hash_secret, verify_secret

from .connection import Database

MAX_ADMINISTRATORS = 3


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Administrator:
    id: str
    name: str
    role: AdminRole
    is_active: bool
    auth_generation: int


class AdminRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_first_admin(self, name: str, password: str, recovery_digest: str) -> Administrator:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("administrator name must not be empty")
        now = utc_now_text()
        administrator = Administrator(
            id=str(uuid4()),
            name=cleaned_name,
            role=AdminRole.SUPERVISOR,
            is_active=True,
            auth_generation=1,
        )
        with self.database.transaction(immediate=True) as connection:
            count = connection.execute("SELECT COUNT(*) FROM administrators").fetchone()[0]
            if count:
                raise ValueError("the first administrator already exists")
            connection.execute(
                """
                INSERT INTO administrators(
                    id, name, role, password_digest, is_active,
                    auth_generation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    administrator.id,
                    administrator.name,
                    administrator.role.value,
                    hash_secret(password).encode(),
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                ("recovery_digest", json.dumps(recovery_digest), now),
            )
            self._audit(connection, administrator.id, "create_supervisor", "administrator", administrator.id)
        return administrator

    def add_admin(self, actor_id: str, name: str, password: str) -> Administrator:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("administrator name must not be empty")
        now = utc_now_text()
        with self.database.transaction(immediate=True) as connection:
            self._require_supervisor(connection, actor_id)
            duplicate = connection.execute(
                """
                SELECT id, role, is_active, auth_generation
                FROM administrators WHERE name = ? COLLATE NOCASE
                """,
                (cleaned_name,),
            ).fetchone()
            if duplicate is not None and duplicate["is_active"]:
                raise ValueError("管理员姓名已经存在")
            count = connection.execute(
                "SELECT COUNT(*) FROM administrators WHERE is_active = 1"
            ).fetchone()[0]
            if count >= MAX_ADMINISTRATORS:
                raise ValueError("administrator limit reached")
            if duplicate is not None:
                if duplicate["role"] != AdminRole.ADMIN.value:
                    raise ValueError("管理员姓名已经存在")
                generation = int(duplicate["auth_generation"]) + 1
                administrator = Administrator(
                    id=duplicate["id"],
                    name=cleaned_name,
                    role=AdminRole.ADMIN,
                    is_active=True,
                    auth_generation=generation,
                )
                connection.execute(
                    """
                    UPDATE administrators
                    SET password_digest = ?, is_active = 1, auth_generation = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (hash_secret(password).encode(), generation, now, administrator.id),
                )
                self._audit(
                    connection,
                    actor_id,
                    "reactivate_admin",
                    "administrator",
                    administrator.id,
                )
            else:
                administrator = Administrator(
                    id=str(uuid4()),
                    name=cleaned_name,
                    role=AdminRole.ADMIN,
                    is_active=True,
                    auth_generation=1,
                )
                connection.execute(
                    """
                    INSERT INTO administrators(
                        id, name, role, password_digest, is_active,
                        auth_generation, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)
                    """,
                    (
                        administrator.id,
                        administrator.name,
                        administrator.role.value,
                        hash_secret(password).encode(),
                        now,
                        now,
                    ),
                )
                self._audit(
                    connection,
                    actor_id,
                    "create_admin",
                    "administrator",
                    administrator.id,
                )
        return administrator

    def deactivate_admin(
        self,
        actor_id: str,
        administrator_id: str,
        *,
        supervisor_password: str,
        reason: str,
    ) -> None:
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise ValueError("请输入移除原因")
        now = utc_now_text()
        with self.database.transaction(immediate=True) as connection:
            actor = connection.execute(
                """
                SELECT role, is_active, password_digest
                FROM administrators WHERE id = ?
                """,
                (actor_id,),
            ).fetchone()
            if (
                actor is None
                or not actor["is_active"]
                or actor["role"] != AdminRole.SUPERVISOR.value
                or not verify_secret(supervisor_password, actor["password_digest"])
            ):
                raise PermissionError("主管理员密码不正确")
            target = connection.execute(
                "SELECT name, role, is_active FROM administrators WHERE id = ?",
                (administrator_id,),
            ).fetchone()
            if target is None:
                raise KeyError(administrator_id)
            if target["role"] is not None and target["role"] != AdminRole.ADMIN.value:
                raise PermissionError("主管理员账户不能被移除")
            if not target["is_active"]:
                raise ValueError("该副管理员已经被移除")
            connection.execute(
                """
                UPDATE administrators
                SET is_active = 0, auth_generation = auth_generation + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, administrator_id),
            )
            connection.execute(
                """
                UPDATE admin_work_authorizations SET revoked_at = ?
                WHERE admin_id = ? AND revoked_at IS NULL
                """,
                (now, administrator_id),
            )
            self._audit(
                connection,
                actor_id,
                "deactivate_admin",
                "administrator",
                administrator_id,
                {"name": target["name"], "reason": cleaned_reason},
            )

    def authenticate(self, name: str, password: str) -> Administrator | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, role, password_digest, is_active, auth_generation
                FROM administrators WHERE name = ? COLLATE NOCASE
                """,
                (name.strip(),),
            ).fetchone()
        if row is None or not row["is_active"]:
            return None
        if not verify_secret(password, row["password_digest"]):
            return None
        return Administrator(
            id=row["id"],
            name=row["name"],
            role=AdminRole(row["role"]),
            is_active=bool(row["is_active"]),
            auth_generation=row["auth_generation"],
        )

    def verify_password(
        self,
        administrator_id: str,
        password: str,
        *,
        supervisor_only: bool = False,
    ) -> Administrator:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, role, password_digest, is_active, auth_generation
                FROM administrators WHERE id = ?
                """,
                (administrator_id,),
            ).fetchone()
        if row is None or not row["is_active"] or not verify_secret(password, row["password_digest"]):
            raise PermissionError("管理员密码不正确")
        administrator = Administrator(
            id=row["id"],
            name=row["name"],
            role=AdminRole(row["role"]),
            is_active=True,
            auth_generation=row["auth_generation"],
        )
        if supervisor_only and administrator.role is not AdminRole.SUPERVISOR:
            raise PermissionError("需要主管理员权限")
        return administrator

    def require_active(self, administrator_id: str) -> Administrator:
        administrator = self.get(administrator_id)
        if not administrator.is_active:
            raise PermissionError("管理员账户已停用")
        return administrator

    def get(self, administrator_id: str) -> Administrator:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, name, role, is_active, auth_generation FROM administrators WHERE id = ?",
                (administrator_id,),
            ).fetchone()
        if row is None:
            raise KeyError(administrator_id)
        return Administrator(
            id=row["id"],
            name=row["name"],
            role=AdminRole(row["role"]),
            is_active=bool(row["is_active"]),
            auth_generation=row["auth_generation"],
        )

    def install_work_admin(
        self,
        administrator_id: str,
        name: str,
        password: str,
        auth_generation: int,
    ) -> Administrator:
        now = utc_now_text()
        administrator = Administrator(
            id=administrator_id,
            name=name.strip(),
            role=AdminRole.ADMIN,
            is_active=True,
            auth_generation=auth_generation,
        )
        with self.database.transaction(immediate=True) as connection:
            count = connection.execute("SELECT COUNT(*) FROM administrators").fetchone()[0]
            if count:
                row = connection.execute(
                    "SELECT id FROM administrators WHERE id = ?",
                    (administrator_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("local administrator database is already initialized")
                return administrator
            connection.execute(
                """
                INSERT INTO administrators(
                    id, name, role, password_digest, is_active,
                    auth_generation, created_at, updated_at
                ) VALUES (?, ?, 'admin', ?, 1, ?, ?, ?)
                """,
                (
                    administrator.id,
                    administrator.name,
                    hash_secret(password).encode(),
                    administrator.auth_generation,
                    now,
                    now,
                ),
            )
            self._audit(connection, administrator.id, "install_work_admin", "administrator", administrator.id)
        return administrator

    def list_all(self) -> list[Administrator]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, role, is_active, auth_generation FROM administrators ORDER BY created_at"
            ).fetchall()
        return [
            Administrator(
                id=row["id"],
                name=row["name"],
                role=AdminRole(row["role"]),
                is_active=bool(row["is_active"]),
                auth_generation=row["auth_generation"],
            )
            for row in rows
        ]

    def reset_supervisor_password(self, recovery_code: str, new_password: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            setting = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = 'recovery_digest'"
            ).fetchone()
            if setting is None:
                raise ValueError("recovery code is not configured")
            recovery_digest = json.loads(setting["value_json"])
            if not verify_secret(recovery_code, recovery_digest):
                raise PermissionError("recovery code is incorrect")
            supervisor = connection.execute(
                "SELECT id FROM administrators WHERE role = 'supervisor'"
            ).fetchone()
            if supervisor is None:
                raise ValueError("supervisor account does not exist")
            now = utc_now_text()
            connection.execute(
                """
                UPDATE administrators SET password_digest = ?, auth_generation = auth_generation + 1,
                    updated_at = ? WHERE id = ?
                """,
                (hash_secret(new_password).encode(), now, supervisor["id"]),
            )
            self._audit(
                connection,
                supervisor["id"],
                "reset_supervisor_with_recovery",
                "administrator",
                supervisor["id"],
            )

    def rotate_supervisor_recovery_code(
        self,
        actor_id: str,
        supervisor_password: str,
    ) -> str:
        recovery_code = generate_recovery_code()
        now = utc_now_text()
        with self.database.transaction(immediate=True) as connection:
            actor = connection.execute(
                """
                SELECT role, is_active, password_digest
                FROM administrators WHERE id = ?
                """,
                (actor_id,),
            ).fetchone()
            if (
                actor is None
                or not actor["is_active"]
                or actor["role"] != AdminRole.SUPERVISOR.value
                or not verify_secret(supervisor_password, actor["password_digest"])
            ):
                raise PermissionError("主管理员密码不正确")
            updated = connection.execute(
                """
                UPDATE app_settings SET value_json = ?, updated_at = ?
                WHERE key = 'recovery_digest'
                """,
                (json.dumps(hash_secret(recovery_code).encode()), now),
            )
            if updated.rowcount != 1:
                raise ValueError("系统恢复码尚未配置")
            self._audit(
                connection,
                actor_id,
                "rotate_supervisor_recovery_code",
                "administrator",
                actor_id,
            )
        return recovery_code

    @staticmethod
    def _require_supervisor(connection, actor_id: str) -> None:
        row = connection.execute(
            "SELECT role, is_active FROM administrators WHERE id = ?",
            (actor_id,),
        ).fetchone()
        if row is None or not row["is_active"] or row["role"] != AdminRole.SUPERVISOR.value:
            raise PermissionError("supervisor permission required")

    @staticmethod
    def _audit(
        connection,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        details: dict | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                id, actor_id, action, entity_type, entity_id, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                actor_id,
                action,
                entity_type,
                entity_id,
                json.dumps(details or {}, ensure_ascii=False),
                utc_now_text(),
            ),
        )
