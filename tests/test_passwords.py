from examdesk.security.passwords import (
    PasswordDigest,
    generate_recovery_code,
    hash_secret,
    verify_secret,
)


def test_secret_hash_round_trip_and_wrong_value() -> None:
    encoded = hash_secret("管理员密码123").encode()

    assert PasswordDigest.decode(encoded).encode() == encoded
    assert verify_secret("管理员密码123", encoded)
    assert not verify_secret("错误密码", encoded)


def test_recovery_code_has_unambiguous_grouped_format() -> None:
    code = generate_recovery_code()
    groups = code.split("-")

    assert len(groups) == 8
    assert all(len(group) == 4 for group in groups)
    assert "0" not in code
    assert "O" not in code

