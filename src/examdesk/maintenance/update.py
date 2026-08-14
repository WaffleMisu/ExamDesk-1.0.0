from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import Version

from examdesk.packages import PasswordPackageCodec, SigningKeyPair, build_archive, read_archive


@dataclass(frozen=True, slots=True)
class AppliedUpdate:
    version: str
    application_directory: Path
    rollback_directory: Path | None


class UpdatePackageBuilder:
    @staticmethod
    def build(
        source_directory: Path,
        *,
        target_version: str,
        minimum_current_version: str,
        distribution_password: str,
        signer: SigningKeyPair,
    ) -> bytes:
        Version(target_version)
        Version(minimum_current_version)
        source_directory = source_directory.resolve()
        files = {}
        file_map = {}
        for path in sorted(source_directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source_directory).as_posix()
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            files[digest] = data
            file_map[relative] = digest
        if not file_map:
            raise ValueError("update source directory is empty")
        manifest = {
            "kind": "software_update",
            "schema_version": 1,
            "target_version": target_version,
            "minimum_current_version": minimum_current_version,
            "created_at": datetime.now(UTC).isoformat(),
            "files": file_map,
        }
        return PasswordPackageCodec.encode(
            build_archive(manifest, files),
            package_kind="software_update",
            password=distribution_password,
            signer=signer,
            minimum_software_version=minimum_current_version,
        )


class OfflineUpdater:
    @staticmethod
    def apply(
        package: bytes,
        *,
        distribution_password: str,
        trusted_signers: dict[str, Ed25519PublicKey],
        current_version: str,
        application_directory: Path,
        active_state_paths: list[Path],
        health_check: Callable[[Path], bool],
    ) -> AppliedUpdate:
        if any(path.exists() for path in active_state_paths):
            raise ValueError("存在未完成考试，禁止更新软件")
        decoded = PasswordPackageCodec.decode(
            package,
            password=distribution_password,
            trusted_signers=trusted_signers,
            expected_kind="software_update",
        )
        archive = read_archive(decoded.payload)
        manifest = archive.manifest
        if manifest.get("kind") != "software_update" or manifest.get("schema_version") != 1:
            raise ValueError("update package manifest is not supported")
        if Version(current_version) < Version(str(manifest["minimum_current_version"])):
            raise ValueError(f"当前版本过低，至少需要{manifest['minimum_current_version']}")
        target_version = str(manifest["target_version"])
        application_directory = application_directory.resolve()
        parent = application_directory.parent
        parent.mkdir(parents=True, exist_ok=True)
        stage = parent / f".{application_directory.name}.update-{uuid4().hex}"
        rollback = parent / f"{application_directory.name}.rollback-{uuid4().hex[:8]}"
        stage.mkdir()
        try:
            for relative_text, digest in manifest["files"].items():
                relative = PurePosixPath(relative_text)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("update package contains an unsafe path")
                data = archive.assets.get(str(digest))
                if data is None:
                    raise ValueError(f"update file is missing: {relative_text}")
                destination = stage.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(destination, data)

            had_existing = application_directory.exists()
            if had_existing:
                os.replace(application_directory, rollback)
            try:
                os.replace(stage, application_directory)
                if not health_check(application_directory):
                    raise RuntimeError("new version health check failed")
            except Exception:
                failed = parent / f".{application_directory.name}.failed-{uuid4().hex}"
                if application_directory.exists():
                    os.replace(application_directory, failed)
                if had_existing and rollback.exists():
                    os.replace(rollback, application_directory)
                if failed.exists():
                    shutil.rmtree(failed)
                raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return AppliedUpdate(
            version=target_version,
            application_directory=application_directory,
            rollback_directory=rollback if rollback.exists() else None,
        )


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="update-", dir=path.parent)
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

