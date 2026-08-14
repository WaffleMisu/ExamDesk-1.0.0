from pathlib import Path

import pytest

from examdesk.db import Database, initialize_database
from examdesk.security import OrganizationKeyError, OrganizationKeyStore


def make_store(tmp_path: Path, name: str) -> OrganizationKeyStore:
    path = tmp_path / f"{name}.sqlite3"
    initialize_database(path)
    return OrganizationKeyStore(Database(path))


def test_organization_keys_round_trip_and_trust_certificate(tmp_path: Path) -> None:
    master = make_store(tmp_path, "master")
    first = master.ensure_initialized()
    restored = master.ensure_initialized()

    assert restored.signing.private_bytes() == first.signing.private_bytes()
    assert restored.result_recipient.private_bytes() == first.result_recipient.private_bytes()
    assert first.signing.id in master.trusted_public_keys()

    candidate = make_store(tmp_path, "candidate")
    signer_id = candidate.import_trust_certificate(
        master.export_trust_certificate(),
        "测试考试组织证书",
    )
    assert signer_id == first.signing.id
    assert signer_id in candidate.trusted_public_keys()


def test_trust_certificate_detects_tampering(tmp_path: Path) -> None:
    master = make_store(tmp_path, "master")
    master.ensure_initialized()
    certificate = bytearray(master.export_trust_certificate())
    certificate[-10] ^= 1

    candidate = make_store(tmp_path, "candidate")
    with pytest.raises(OrganizationKeyError):
        candidate.import_trust_certificate(bytes(certificate))


def test_candidate_can_keep_multiple_trust_certificates(tmp_path: Path) -> None:
    first_master = make_store(tmp_path, "first-master")
    second_master = make_store(tmp_path, "second-master")
    first_keys = first_master.ensure_initialized()
    second_keys = second_master.ensure_initialized()
    candidate = make_store(tmp_path, "multi-certificate-candidate")

    first_id = candidate.import_trust_certificate(
        first_master.export_trust_certificate(),
        "first.examtrust",
    )
    second_id = candidate.import_trust_certificate(
        second_master.export_trust_certificate(),
        "second.examtrust",
    )
    repeated_id = candidate.import_trust_certificate(
        first_master.export_trust_certificate(),
        "first-copy.examtrust",
    )

    trusted = candidate.trusted_public_keys()
    assert first_id == first_keys.signing.id
    assert second_id == second_keys.signing.id
    assert repeated_id == first_id
    assert set(trusted) == {first_id, second_id}
