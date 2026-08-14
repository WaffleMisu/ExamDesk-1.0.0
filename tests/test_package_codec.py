import pytest

from examdesk.packages import (
    PackageError,
    PasswordPackageCodec,
    RecipientPackageCodec,
    SigningKeyPair,
    X25519KeyPair,
)


def test_password_package_round_trip_and_json_payload() -> None:
    signer = SigningKeyPair.generate()
    package = PasswordPackageCodec.encode_json(
        {"session": "六月测试", "questions": [1, 2, 3]},
        package_kind="exam",
        password="600000",
        signer=signer,
        minimum_software_version="2.0.0",
    )

    decoded = PasswordPackageCodec.decode(
        package,
        password="600000",
        trusted_signers={signer.id: signer.public_key},
        expected_kind="exam",
    )

    assert decoded.json()["session"] == "六月测试"
    assert decoded.header["issuer_key_id"] == signer.id


def test_open_password_package_round_trip_without_user_password() -> None:
    signer = SigningKeyPair.generate()
    package = PasswordPackageCodec.encode_json(
        {"session": "开放练习", "questions": [1, 2]},
        package_kind="practice",
        password="",
        signer=signer,
        minimum_software_version="2.2.2",
    )

    assert PasswordPackageCodec.requires_password(package) is False
    decoded = PasswordPackageCodec.decode(
        package,
        password="",
        trusted_signers={signer.id: signer.public_key},
        expected_kind="practice",
    )

    assert decoded.json()["session"] == "开放练习"
    assert decoded.header["format_version"] == 2
    assert decoded.header["access_mode"] == "open"
    with pytest.raises(PackageError, match="trusted"):
        PasswordPackageCodec.decode(package, password="", trusted_signers={})
    tampered = package[:-1] + bytes([package[-1] ^ 1])
    with pytest.raises(PackageError, match="hash"):
        PasswordPackageCodec.decode(
            tampered,
            password="",
            trusted_signers={signer.id: signer.public_key},
        )


def test_password_package_reports_that_password_is_required() -> None:
    signer = SigningKeyPair.generate()
    package = PasswordPackageCodec.encode(
        b"payload",
        package_kind="exam",
        password="600000",
        signer=signer,
        minimum_software_version="2.0.0",
    )

    assert PasswordPackageCodec.requires_password(package) is True
    with pytest.raises(PackageError, match="required"):
        PasswordPackageCodec.decode(
            package,
            password="",
            trusted_signers={signer.id: signer.public_key},
        )


def test_password_package_rejects_wrong_password_untrusted_signer_and_tampering() -> None:
    signer = SigningKeyPair.generate()
    package = PasswordPackageCodec.encode(
        b"payload",
        package_kind="exam",
        password="1998",
        signer=signer,
        minimum_software_version="2.0.0",
    )

    with pytest.raises(PackageError, match="password"):
        PasswordPackageCodec.decode(
            package,
            password="wrong",
            trusted_signers={signer.id: signer.public_key},
        )

    with pytest.raises(PackageError, match="trusted"):
        PasswordPackageCodec.decode(package, password="1998", trusted_signers={})

    tampered = package[:-1] + bytes([package[-1] ^ 1])
    with pytest.raises(PackageError, match="hash"):
        PasswordPackageCodec.decode(
            tampered,
            password="1998",
            trusted_signers={signer.id: signer.public_key},
        )


def test_recipient_result_can_only_be_opened_by_recipient_with_session_proof() -> None:
    recipient = X25519KeyPair.generate()
    other_recipient = X25519KeyPair.generate()
    session_key = b"s" * 32
    package = RecipientPackageCodec.encode_json(
        {"candidate": "测试用户甲", "strict_score": "15"},
        package_kind="result",
        recipient_public_key=recipient.public_key,
        session_auth_key=session_key,
    )

    decoded = RecipientPackageCodec.decode(
        package,
        recipient=recipient,
        session_auth_key=session_key,
        expected_kind="result",
    )
    assert decoded.json()["candidate"] == "测试用户甲"

    with pytest.raises(PackageError, match="another"):
        RecipientPackageCodec.decode(
            package,
            recipient=other_recipient,
            session_auth_key=session_key,
        )

    with pytest.raises(PackageError, match="session"):
        RecipientPackageCodec.decode(
            package,
            recipient=recipient,
            session_auth_key=b"x" * 32,
        )


def test_private_key_round_trip_keeps_key_identity() -> None:
    signing = SigningKeyPair.generate()
    encryption = X25519KeyPair.generate()

    assert SigningKeyPair.from_private_bytes(signing.private_bytes()).id == signing.id
    assert X25519KeyPair.from_private_bytes(encryption.private_bytes()).id == encryption.id
