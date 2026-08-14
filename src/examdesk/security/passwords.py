from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32
RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


@dataclass(frozen=True, slots=True)
class PasswordDigest:
    salt: bytes
    digest: bytes
    n: int = SCRYPT_N
    r: int = SCRYPT_R
    p: int = SCRYPT_P

    def encode(self) -> str:
        salt_text = base64.urlsafe_b64encode(self.salt).decode("ascii")
        digest_text = base64.urlsafe_b64encode(self.digest).decode("ascii")
        return f"scrypt${self.n}${self.r}${self.p}${salt_text}${digest_text}"

    @classmethod
    def decode(cls, value: str) -> PasswordDigest:
        try:
            algorithm, n, r, p, salt_text, digest_text = value.split("$")
            if algorithm != "scrypt":
                raise ValueError("unsupported password algorithm")
            return cls(
                salt=base64.urlsafe_b64decode(salt_text.encode("ascii")),
                digest=base64.urlsafe_b64decode(digest_text.encode("ascii")),
                n=int(n),
                r=int(r),
                p=int(p),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid password digest") from exc


def _normalized_secret(secret: str) -> bytes:
    if not isinstance(secret, str) or not secret:
        raise ValueError("secret must not be empty")
    return secret.encode("utf-8")


def hash_secret(secret: str, *, salt: bytes | None = None) -> PasswordDigest:
    actual_salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        _normalized_secret(secret),
        salt=actual_salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LENGTH,
    )
    return PasswordDigest(salt=actual_salt, digest=digest)


def verify_secret(secret: str, encoded_digest: str) -> bool:
    try:
        stored = PasswordDigest.decode(encoded_digest)
        candidate = hashlib.scrypt(
            _normalized_secret(secret),
            salt=stored.salt,
            n=stored.n,
            r=stored.r,
            p=stored.p,
            dklen=len(stored.digest),
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate, stored.digest)


def generate_recovery_code(group_count: int = 8, group_size: int = 4) -> str:
    groups = [
        "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(group_size))
        for _ in range(group_count)
    ]
    return "-".join(groups)

