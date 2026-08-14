from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

from examdesk.db.admin_repository import AdminRepository
from examdesk.domain.enums import AdminRole
from examdesk.packages import (
    PackageError,
    PasswordPackageCodec,
    RecipientPackageCodec,
    SigningKeyPair,
    X25519KeyPair,
    build_archive,
    read_archive,
)

from .assets import AssetManager
from .repository import QuestionRepository, QuestionVersionConflict
from .serialization import question_from_payload, question_payload_hash, question_to_payload

WORK_CONTEXT_KEY = "bank_work_context"
BANK_ID_KEY = "bank_id"


@dataclass(frozen=True, slots=True)
class InstalledWorkPackage:
    bank_id: str
    admin_id: str
    admin_name: str
    question_count: int
    base_revision: int


@dataclass(frozen=True, slots=True)
class PatchConflict:
    question_id: str
    source_location: str
    reason: str


@dataclass(slots=True)
class PatchImportResult:
    applied: list[str] = field(default_factory=list)
    conflicts: list[PatchConflict] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    replayed: bool = False


class BankCollaborationService:
    def __init__(
        self,
        database,
        question_repository: QuestionRepository,
        asset_manager: AssetManager,
        admin_repository: AdminRepository,
    ) -> None:
        self.database = database
        self.questions = question_repository
        self.assets = asset_manager
        self.admins = admin_repository

    def issue_work_package(
        self,
        *,
        admin_id: str,
        package_password: str,
        signer: SigningKeyPair,
        master_recipient: X25519KeyPair,
        minimum_software_version: str,
    ) -> bytes:
        administrator = self.admins.get(admin_id)
        if administrator.role is not AdminRole.ADMIN or not administrator.is_active:
            raise PermissionError("work package requires an active ordinary administrator")
        bank_id = self._bank_id()
        base_revision = self.questions.bank_revision()
        question_payloads, versions, hashes, asset_files = self._export_current_questions()
        patch_secret = secrets.token_bytes(32)
        work_id = str(uuid4())
        manifest = {
            "kind": "bank_work",
            "schema_version": 1,
            "work_id": work_id,
            "bank_id": bank_id,
            "base_revision": base_revision,
            "admin": {
                "id": administrator.id,
                "name": administrator.name,
                "auth_generation": administrator.auth_generation,
            },
            "patch_secret": _b64(patch_secret),
            "master_recipient_public_key": _b64(master_recipient.public_bytes),
            "question_versions": versions,
            "question_hashes": hashes,
            "questions": question_payloads,
        }
        package = PasswordPackageCodec.encode(
            build_archive(manifest, asset_files),
            package_kind="bank_work",
            password=package_password,
            signer=signer,
            minimum_software_version=minimum_software_version,
            package_id=work_id,
        )
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE admin_work_authorizations SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL",
                (now, administrator.id),
            )
            connection.execute(
                """
                INSERT INTO admin_work_authorizations(
                    id, admin_id, bank_id, patch_secret, base_revision,
                    auth_generation, issued_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_id,
                    administrator.id,
                    bank_id,
                    patch_secret,
                    base_revision,
                    administrator.auth_generation,
                    now,
                ),
            )
        return package

    def install_work_package(
        self,
        package: bytes,
        *,
        package_password: str,
        local_login_password: str,
        trusted_signers: dict[str, Ed25519PublicKey],
    ) -> InstalledWorkPackage:
        if len(local_login_password) < 8:
            raise ValueError("本机登录密码至少需要8个字符")
        if self.questions.list_current():
            raise ValueError("local work database already contains questions")
        decoded = PasswordPackageCodec.decode(
            package,
            password=package_password,
            trusted_signers=trusted_signers,
            expected_kind="bank_work",
        )
        archive = read_archive(decoded.payload)
        manifest = archive.manifest
        _require_manifest(manifest, "bank_work")
        asset_ids = self._ingest_archive_assets(archive.assets)
        for payload in manifest.get("questions", []):
            self.questions.create(question_from_payload(payload, asset_ids), actor_id=None)

        administrator = manifest["admin"]
        self.admins.install_work_admin(
            str(administrator["id"]),
            str(administrator["name"]),
            local_login_password,
            int(administrator["auth_generation"]),
        )
        context = {
            "bank_id": manifest["bank_id"],
            "base_revision": manifest["base_revision"],
            "admin": administrator,
            "patch_secret": manifest["patch_secret"],
            "master_recipient_public_key": manifest["master_recipient_public_key"],
            "question_versions": manifest["question_versions"],
            "question_hashes": manifest["question_hashes"],
        }
        self._set_setting(WORK_CONTEXT_KEY, context)
        self._set_setting(BANK_ID_KEY, manifest["bank_id"])
        return InstalledWorkPackage(
            bank_id=str(manifest["bank_id"]),
            admin_id=str(administrator["id"]),
            admin_name=str(administrator["name"]),
            question_count=len(manifest.get("questions", [])),
            base_revision=int(manifest["base_revision"]),
        )

    def installed_work_package(self) -> InstalledWorkPackage | None:
        context = self._get_setting(WORK_CONTEXT_KEY)
        if not isinstance(context, dict):
            return None
        administrator = context.get("admin")
        if not isinstance(administrator, dict):
            return None
        try:
            return InstalledWorkPackage(
                bank_id=str(context["bank_id"]),
                admin_id=str(administrator["id"]),
                admin_name=str(administrator["name"]),
                question_count=len(self.questions.list_current()),
                base_revision=int(context["base_revision"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def export_patch(self) -> bytes:
        context = self._get_setting(WORK_CONTEXT_KEY)
        if not isinstance(context, dict):
            raise ValueError("this database was not initialized from a work package")
        question_payloads, _, hashes, asset_files = self._export_current_questions()
        base_hashes = context["question_hashes"]
        base_versions = context["question_versions"]
        operations = []
        for payload in question_payloads:
            question_id = str(payload["id"])
            if hashes[question_id] == base_hashes.get(question_id):
                continue
            operations.append(
                {
                    "operation": "create" if question_id not in base_versions else "update",
                    "base_master_version": int(base_versions.get(question_id, 0)),
                    "question": payload,
                    "source_location": payload.get("display_number", question_id),
                }
            )
        manifest = {
            "kind": "bank_patch",
            "schema_version": 1,
            "bank_id": context["bank_id"],
            "base_revision": context["base_revision"],
            "admin": context["admin"],
            "operations": operations,
        }
        master_public_key = X25519PublicKey.from_public_bytes(
            _unb64(context["master_recipient_public_key"])
        )
        return RecipientPackageCodec.encode(
            build_archive(manifest, asset_files),
            package_kind="bank_patch",
            recipient_public_key=master_public_key,
            session_auth_key=_unb64(context["patch_secret"]),
        )

    def import_patch(
        self,
        package: bytes,
        *,
        master_recipient: X25519KeyPair,
        imported_by: str,
        source_path: str = "",
    ) -> PatchImportResult:
        file_hash = hashlib.sha256(package).hexdigest()
        with self.database.connect() as connection:
            replay = connection.execute(
                "SELECT id FROM package_imports WHERE file_hash = ?",
                (file_hash,),
            ).fetchone()
            authorizations = connection.execute(
                """
                SELECT * FROM admin_work_authorizations
                WHERE revoked_at IS NULL ORDER BY issued_at DESC
                """
            ).fetchall()
        if replay is not None:
            return PatchImportResult(replayed=True)

        decoded = None
        authorization = None
        for row in authorizations:
            try:
                candidate = RecipientPackageCodec.decode(
                    package,
                    recipient=master_recipient,
                    session_auth_key=row["patch_secret"],
                    expected_kind="bank_patch",
                )
            except PackageError:
                continue
            decoded = candidate
            authorization = row
            break
        if decoded is None or authorization is None:
            raise PackageError("bank patch authorization is invalid or has been revoked")

        archive = read_archive(decoded.payload)
        manifest = archive.manifest
        _require_manifest(manifest, "bank_patch")
        administrator = manifest.get("admin", {})
        if str(administrator.get("id")) != authorization["admin_id"]:
            raise PackageError("bank patch administrator does not match authorization")
        if int(administrator.get("auth_generation", 0)) != authorization["auth_generation"]:
            raise PackageError("bank patch administrator authorization is outdated")
        if manifest.get("bank_id") != self._bank_id():
            raise PackageError("bank patch belongs to another question bank")

        asset_ids = self._ingest_archive_assets(archive.assets)
        result = PatchImportResult()
        for operation in manifest.get("operations", []):
            payload = operation.get("question", {})
            question_id = str(payload.get("id", ""))
            source_location = str(operation.get("source_location", question_id))
            try:
                draft = question_from_payload(payload, asset_ids)
                base_version = int(operation.get("base_master_version", 0))
                existing_version = self._current_version(question_id)
                duplicate_ids = {
                    match.id
                    for match in self.questions.find_exact_duplicates(draft)
                    if match.id != question_id
                }
                if duplicate_ids:
                    result.conflicts.append(
                        PatchConflict(question_id, source_location, "与主题库其他题目完全重复")
                    )
                    continue
                if operation.get("operation") == "create":
                    if existing_version is not None:
                        result.conflicts.append(
                            PatchConflict(question_id, source_location, "新增题目编号已存在")
                        )
                        continue
                    surface_conflicts = {
                        match.id for match in self.questions.find_surface_conflicts(draft)
                    }
                    if surface_conflicts:
                        result.conflicts.append(
                            PatchConflict(
                                question_id,
                                source_location,
                                "题面和图片相同，但答案或分值与主题库不一致",
                            )
                        )
                        continue
                    self.questions.create(draft, imported_by)
                elif operation.get("operation") == "update":
                    if existing_version != base_version:
                        result.conflicts.append(
                            PatchConflict(question_id, source_location, "主题库题目已被其他人修改")
                        )
                        continue
                    self.questions.update(draft, imported_by, expected_version=base_version)
                else:
                    raise ValueError("unknown patch operation")
                result.applied.append(question_id)
            except (KeyError, QuestionVersionConflict, ValueError) as exc:
                result.errors.append((source_location, str(exc)))

        self._record_import(decoded.header["package_id"], file_hash, source_path, imported_by, result)
        return result

    def _export_current_questions(self):
        payloads = []
        versions = {}
        hashes = {}
        files: dict[str, bytes] = {}
        for question, version in self.questions.list_current():
            asset_ids = list(question.question_asset_ids)
            asset_ids.extend(asset_id for option in question.options for asset_id in option.asset_ids)
            asset_sha_by_id = {}
            for asset_id in asset_ids:
                record = self.assets.get(asset_id)
                asset_sha_by_id[asset_id] = record.sha256
                files.setdefault(record.sha256, self.assets.absolute_path(record).read_bytes())
            payload = question_to_payload(question, asset_sha_by_id)
            payloads.append(payload)
            versions[question.id] = version
            hashes[question.id] = question_payload_hash(payload)
        return payloads, versions, hashes, files

    def _ingest_archive_assets(self, assets: dict[str, bytes]) -> dict[str, str]:
        result = {}
        for digest, data in assets.items():
            record = self.assets.ingest_archive_bytes(data, digest)
            result[digest] = record.id
        return result

    def _bank_id(self) -> str:
        value = self._get_setting(BANK_ID_KEY)
        if isinstance(value, str) and value:
            return value
        bank_id = str(uuid4())
        self._set_setting(BANK_ID_KEY, bank_id)
        return bank_id

    def _get_setting(self, key: str):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return json.loads(row["value_json"]) if row is not None else None

    def _set_setting(self, key: str, value) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False, sort_keys=True), now),
            )

    def _current_version(self, question_id: str) -> int | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT current_version FROM questions WHERE id = ?",
                (question_id,),
            ).fetchone()
        return int(row["current_version"]) if row is not None else None

    def _record_import(
        self,
        package_id: str,
        file_hash: str,
        source_path: str,
        imported_by: str,
        result: PatchImportResult,
    ) -> None:
        summary = {
            "applied": len(result.applied),
            "conflicts": len(result.conflicts),
            "errors": len(result.errors),
        }
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO package_imports(
                    id, package_kind, package_id, file_hash, source_path,
                    imported_by, imported_at, result_json
                ) VALUES (?, 'bank_patch', ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    package_id,
                    file_hash,
                    source_path,
                    imported_by,
                    datetime.now(UTC).isoformat(),
                    json.dumps(summary, ensure_ascii=False),
                ),
            )


def _require_manifest(manifest: dict, expected_kind: str) -> None:
    if manifest.get("kind") != expected_kind or manifest.get("schema_version") != 1:
        raise PackageError("unsupported collaboration package manifest")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PackageError("invalid collaboration package key") from exc
