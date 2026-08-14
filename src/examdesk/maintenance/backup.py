from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from examdesk.db import Database, initialize_database
from examdesk.packages import PasswordPackageCodec, build_archive, read_archive
from examdesk.security import OrganizationKeyStore


@dataclass(frozen=True, slots=True)
class RestoredBackup:
    database_path: Path
    asset_count: int
    backup_created_at: str


class BackupService:
    def __init__(self, database: Database, asset_root: Path) -> None:
        self.database = database
        self.asset_root = asset_root.resolve()

    def create(
        self,
        destination: Path,
        *,
        password: str,
        key_store: OrganizationKeyStore,
        software_version: str,
        automatic: bool = False,
    ) -> Path:
        if len(password) < 8:
            raise ValueError("备份密码至少需要8个字符")
        keys = key_store.load()
        database_bytes = self._consistent_database_bytes()
        database_sha = hashlib.sha256(database_bytes).hexdigest()
        files = {database_sha: database_bytes}
        asset_shas = []
        with self.database.connect() as connection:
            rows = connection.execute("SELECT sha256, relative_path FROM assets").fetchall()
        for row in rows:
            path = (self.asset_root / Path(row["relative_path"])).resolve()
            if self.asset_root != path and self.asset_root not in path.parents:
                raise ValueError("asset path escapes the asset directory")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError(f"asset content hash does not match: {row['relative_path']}")
            files[row["sha256"]] = data
            asset_shas.append(row["sha256"])
        manifest = {
            "kind": "full_backup",
            "schema_version": 2,
            "created_at": datetime.now(UTC).isoformat(),
            "software_version": software_version,
            "database_sha256": database_sha,
            "asset_sha256": sorted(asset_shas),
            "automatic": automatic,
            "portable_keys": key_store.export_portable_keys(),
        }
        package = PasswordPackageCodec.encode(
            build_archive(manifest, files),
            package_kind="full_backup",
            password=password,
            signer=keys.signing,
            minimum_software_version=software_version,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, package)
        return destination

    @staticmethod
    def restore(
        package_path: Path,
        *,
        password: str,
        trusted_signers: dict[str, Ed25519PublicKey],
        target_database: Path,
        target_asset_root: Path,
    ) -> RestoredBackup:
        decoded = PasswordPackageCodec.decode(
            package_path.read_bytes(),
            password=password,
            trusted_signers=trusted_signers,
            expected_kind="full_backup",
        )
        archive = read_archive(decoded.payload)
        manifest = archive.manifest
        if manifest.get("kind") != "full_backup" or manifest.get("schema_version") != 2:
            raise ValueError("backup manifest is not supported")
        database_sha = str(manifest["database_sha256"])
        database_bytes = archive.assets.get(database_sha)
        if database_bytes is None or hashlib.sha256(database_bytes).hexdigest() != database_sha:
            raise ValueError("backup database file is missing or damaged")

        target_database = target_database.resolve()
        target_database.parent.mkdir(parents=True, exist_ok=True)
        temporary_database = target_database.with_name(
            f".{target_database.name}.restore-{uuid4().hex}.tmp"
        )
        _atomic_write(temporary_database, database_bytes)
        temporary_assets: Path | None = None
        try:
            initialize_database(temporary_database)
            restored_database = Database(temporary_database)
            with restored_database.connect() as connection:
                asset_rows = connection.execute(
                    "SELECT sha256, relative_path FROM assets"
                ).fetchall()
            portable_keys = manifest.get("portable_keys")
            if not isinstance(portable_keys, dict):
                raise ValueError("备份中缺少可移植组织私钥")
            OrganizationKeyStore(restored_database).rebind_portable_keys(portable_keys)

            target_asset_root = target_asset_root.resolve()
            target_asset_root.parent.mkdir(parents=True, exist_ok=True)
            temporary_assets = Path(
                tempfile.mkdtemp(
                    prefix=f".{target_asset_root.name}.restore-",
                    dir=target_asset_root.parent,
                )
            ).resolve()
            for row in asset_rows:
                data = archive.assets.get(row["sha256"])
                if data is None or hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise ValueError(f"backup asset is missing or damaged: {row['sha256']}")
                destination = (temporary_assets / Path(row["relative_path"])).resolve()
                if temporary_assets != destination and temporary_assets not in destination.parents:
                    raise ValueError("backup contains an unsafe asset path")
                destination.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(destination, data)
            _replace_restored_data(
                temporary_database,
                temporary_assets,
                target_database,
                target_asset_root,
            )
        finally:
            temporary_database.unlink(missing_ok=True)
            if temporary_assets is not None:
                shutil.rmtree(temporary_assets, ignore_errors=True)
        return RestoredBackup(
            database_path=target_database,
            asset_count=len(asset_rows),
            backup_created_at=str(manifest["created_at"]),
        )

    @staticmethod
    def prune_automatic_backups(directory: Path, keep: int = 30) -> list[Path]:
        if keep < 1:
            raise ValueError("at least one automatic backup must be retained")
        backups = sorted(
            directory.glob("auto_*.exambackup"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        removed = []
        for path in backups[keep:]:
            path.unlink()
            removed.append(path)
        return removed

    def _consistent_database_bytes(self) -> bytes:
        descriptor, temporary_name = tempfile.mkstemp(suffix=".sqlite3")
        os.close(descriptor)
        try:
            source = self.database.connect()
            destination = sqlite3.connect(temporary_name)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            return Path(temporary_name).read_bytes()
        finally:
            with contextlib.suppress(FileNotFoundError):
                Path(temporary_name).unlink()


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="backup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def _replace_restored_data(
    temporary_database: Path,
    temporary_assets: Path,
    target_database: Path,
    target_assets: Path,
) -> None:
    token = uuid4().hex
    previous_database = target_database.with_name(f".{target_database.name}.before-{token}")
    previous_assets = target_assets.with_name(f".{target_assets.name}.before-{token}")
    database_moved = False
    assets_moved = False
    try:
        if target_database.exists():
            os.replace(target_database, previous_database)
            database_moved = True
        if target_assets.exists():
            os.replace(target_assets, previous_assets)
            assets_moved = True
        os.replace(temporary_database, target_database)
        os.replace(temporary_assets, target_assets)
    except Exception:
        target_database.unlink(missing_ok=True)
        shutil.rmtree(target_assets, ignore_errors=True)
        if database_moved and previous_database.exists():
            os.replace(previous_database, target_database)
        if assets_moved and previous_assets.exists():
            os.replace(previous_assets, target_assets)
        raise
    previous_database.unlink(missing_ok=True)
    shutil.rmtree(previous_assets, ignore_errors=True)
