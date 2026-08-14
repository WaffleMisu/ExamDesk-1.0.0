from pathlib import Path

import pytest

from examdesk.maintenance import OfflineUpdater, UpdatePackageBuilder
from examdesk.packages import SigningKeyPair


def make_update_package(tmp_path: Path, signer: SigningKeyPair) -> bytes:
    source = tmp_path / "new-version"
    source.mkdir()
    (source / "app.exe").write_bytes(b"new executable")
    (source / "version.txt").write_text("2.0.1", encoding="utf-8")
    return UpdatePackageBuilder.build(
        source,
        target_version="2.0.1",
        minimum_current_version="2.0.0",
        distribution_password="update-key",
        signer=signer,
    )


def test_offline_update_applies_and_keeps_rollback(tmp_path: Path) -> None:
    signer = SigningKeyPair.generate()
    package = make_update_package(tmp_path, signer)
    application = tmp_path / "app"
    application.mkdir()
    (application / "app.exe").write_bytes(b"old executable")

    applied = OfflineUpdater.apply(
        package,
        distribution_password="update-key",
        trusted_signers={signer.id: signer.public_key},
        current_version="2.0.0",
        application_directory=application,
        active_state_paths=[tmp_path / "active.state"],
        health_check=lambda path: (path / "version.txt").read_text(encoding="utf-8") == "2.0.1",
    )

    assert (application / "app.exe").read_bytes() == b"new executable"
    assert applied.rollback_directory is not None
    assert (applied.rollback_directory / "app.exe").read_bytes() == b"old executable"


def test_offline_update_rolls_back_failed_health_check_and_blocks_active_exam(tmp_path: Path) -> None:
    signer = SigningKeyPair.generate()
    package = make_update_package(tmp_path, signer)
    application = tmp_path / "app"
    application.mkdir()
    (application / "app.exe").write_bytes(b"old executable")

    with pytest.raises(RuntimeError, match="health"):
        OfflineUpdater.apply(
            package,
            distribution_password="update-key",
            trusted_signers={signer.id: signer.public_key},
            current_version="2.0.0",
            application_directory=application,
            active_state_paths=[],
            health_check=lambda path: False,
        )
    assert (application / "app.exe").read_bytes() == b"old executable"

    active_state = tmp_path / "active.state"
    active_state.write_text("active", encoding="utf-8")
    with pytest.raises(ValueError, match="未完成考试"):
        OfflineUpdater.apply(
            package,
            distribution_password="update-key",
            trusted_signers={signer.id: signer.public_key},
            current_version="2.0.0",
            application_directory=application,
            active_state_paths=[active_state],
            health_check=lambda path: True,
        )

