from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PASSWORD_MAGIC = b"EXDKPW10"
RECIPIENT_MAGIC = b"EXDKRC10"
HEADER_PREFIX_SIZE = 12
MAX_HEADER_SIZE = 64 * 1024
FORMAT_VERSION = 1
OPEN_FORMAT_VERSION = 2


class PackageError(ValueError):
    pass


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PackageError("invalid base64 value in package header") from exc


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _key_id(public_bytes: bytes) -> str:
    return hashlib.sha256(public_bytes).hexdigest()[:24]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class SigningKeyPair:
    private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls) -> SigningKeyPair:
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    @property
    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def id(self) -> str:
        return _key_id(self.public_bytes)

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @classmethod
    def from_private_bytes(cls, value: bytes) -> SigningKeyPair:
        return cls(Ed25519PrivateKey.from_private_bytes(value))


@dataclass(frozen=True, slots=True)
class X25519KeyPair:
    private_key: X25519PrivateKey

    @classmethod
    def generate(cls) -> X25519KeyPair:
        return cls(X25519PrivateKey.generate())

    @property
    def public_key(self) -> X25519PublicKey:
        return self.private_key.public_key()

    @property
    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def id(self) -> str:
        return _key_id(self.public_bytes)

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @classmethod
    def from_private_bytes(cls, value: bytes) -> X25519KeyPair:
        return cls(X25519PrivateKey.from_private_bytes(value))


@dataclass(frozen=True, slots=True)
class DecodedPackage:
    payload: bytes
    header: dict[str, Any]

    def json(self) -> dict[str, Any]:
        try:
            value = json.loads(self.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageError("package payload is not valid JSON") from exc
        if not isinstance(value, dict):
            raise PackageError("package JSON payload must be an object")
        return value


class PasswordPackageCodec:
    @staticmethod
    def requires_password(package: bytes) -> bool:
        header, _ciphertext = _unpack(PASSWORD_MAGIC, package)
        return not (
            header.get("format_version") == OPEN_FORMAT_VERSION
            and header.get("access_mode") == "open"
        )

    @staticmethod
    def encode(
        payload: bytes,
        *,
        package_kind: str,
        password: str,
        signer: SigningKeyPair,
        minimum_software_version: str,
        package_id: str | None = None,
    ) -> bytes:
        nonce = secrets.token_bytes(12)
        package_id = package_id or str(uuid4())
        if password:
            salt = secrets.token_bytes(16)
            key = _derive_password_key(password, salt)
            core_header: dict[str, Any] = {
                "created_at": _utc_now(),
                "encryption": "scrypt-aes256-gcm",
                "format_version": FORMAT_VERSION,
                "issuer_key_id": signer.id,
                "kind": package_kind,
                "minimum_software_version": minimum_software_version,
                "nonce": _b64(nonce),
                "package_id": package_id,
                "salt": _b64(salt),
            }
        else:
            key = secrets.token_bytes(32)
            core_header = {
                "access_mode": "open",
                "content_key": _b64(key),
                "created_at": _utc_now(),
                "encryption": "aes256-gcm-open",
                "format_version": OPEN_FORMAT_VERSION,
                "issuer_key_id": signer.id,
                "kind": package_kind,
                "minimum_software_version": minimum_software_version,
                "nonce": _b64(nonce),
                "package_id": package_id,
            }
        aad = _canonical_json(core_header)
        ciphertext = AESGCM(key).encrypt(nonce, payload, aad)
        signed_header = dict(core_header)
        signed_header["ciphertext_sha256"] = hashlib.sha256(ciphertext).hexdigest()
        signature = signer.private_key.sign(_canonical_json(signed_header) + ciphertext)
        final_header = dict(signed_header)
        final_header["signature"] = _b64(signature)
        return _pack(PASSWORD_MAGIC, final_header, ciphertext)

    @staticmethod
    def encode_json(
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> bytes:
        return PasswordPackageCodec.encode(_canonical_json(payload), **kwargs)

    @staticmethod
    def decode(
        package: bytes,
        *,
        password: str,
        trusted_signers: dict[str, Ed25519PublicKey],
        expected_kind: str | None = None,
    ) -> DecodedPackage:
        header, ciphertext = _unpack(PASSWORD_MAGIC, package)
        signature_text = header.pop("signature", None)
        if not isinstance(signature_text, str):
            raise PackageError("package signature is missing")
        issuer_key_id = header.get("issuer_key_id")
        public_key = trusted_signers.get(str(issuer_key_id))
        if public_key is None:
            raise PackageError("package signer is not trusted")
        if expected_kind is not None and header.get("kind") != expected_kind:
            raise PackageError("unexpected package kind")
        if header.get("format_version") not in (FORMAT_VERSION, OPEN_FORMAT_VERSION):
            raise PackageError("unsupported package format version")
        if hashlib.sha256(ciphertext).hexdigest() != header.get("ciphertext_sha256"):
            raise PackageError("package content hash does not match")
        try:
            public_key.verify(_unb64(signature_text), _canonical_json(header) + ciphertext)
        except InvalidSignature as exc:
            raise PackageError("package signature is invalid") from exc

        format_version = header.get("format_version")
        core_header = dict(header)
        core_header.pop("ciphertext_sha256", None)
        try:
            nonce = _unb64(str(core_header["nonce"]))
            if format_version == FORMAT_VERSION:
                if not password:
                    raise PackageError("package password is required")
                salt = _unb64(str(core_header["salt"]))
                key = _derive_password_key(password, salt)
            elif (
                format_version == OPEN_FORMAT_VERSION
                and core_header.get("access_mode") == "open"
                and core_header.get("encryption") == "aes256-gcm-open"
            ):
                key = _unb64(str(core_header["content_key"]))
                if len(key) != 32:
                    raise PackageError("open package content key is invalid")
            else:
                raise PackageError("unsupported package format version")
            payload = AESGCM(key).decrypt(nonce, ciphertext, _canonical_json(core_header))
        except PackageError:
            raise
        except (InvalidTag, KeyError, ValueError) as exc:
            raise PackageError("password is incorrect or package is damaged") from exc
        return DecodedPackage(payload=payload, header=header)


class RecipientPackageCodec:
    @staticmethod
    def encode(
        payload: bytes,
        *,
        package_kind: str,
        recipient_public_key: X25519PublicKey,
        session_auth_key: bytes,
        package_id: str | None = None,
    ) -> bytes:
        if len(session_auth_key) < 16:
            raise ValueError("session authentication key is too short")
        ephemeral_private = X25519PrivateKey.generate()
        ephemeral_public_bytes = ephemeral_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        recipient_public_bytes = recipient_public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        shared_secret = ephemeral_private.exchange(recipient_public_key)
        key = _derive_recipient_key(shared_secret, salt)
        core_header: dict[str, Any] = {
            "created_at": _utc_now(),
            "encryption": "x25519-hkdf-aes256-gcm",
            "ephemeral_public_key": _b64(ephemeral_public_bytes),
            "format_version": FORMAT_VERSION,
            "kind": package_kind,
            "nonce": _b64(nonce),
            "package_id": package_id or str(uuid4()),
            "recipient_key_id": _key_id(recipient_public_bytes),
            "salt": _b64(salt),
        }
        aad = _canonical_json(core_header)
        ciphertext = AESGCM(key).encrypt(nonce, payload, aad)
        proof = hmac.new(session_auth_key, aad + ciphertext, hashlib.sha256).digest()
        final_header = dict(core_header)
        final_header["session_proof"] = _b64(proof)
        return _pack(RECIPIENT_MAGIC, final_header, ciphertext)

    @staticmethod
    def encode_json(payload: dict[str, Any], **kwargs: Any) -> bytes:
        return RecipientPackageCodec.encode(_canonical_json(payload), **kwargs)

    @staticmethod
    def decode(
        package: bytes,
        *,
        recipient: X25519KeyPair,
        session_auth_key: bytes,
        expected_kind: str | None = None,
    ) -> DecodedPackage:
        header, ciphertext = _unpack(RECIPIENT_MAGIC, package)
        proof_text = header.pop("session_proof", None)
        if not isinstance(proof_text, str):
            raise PackageError("result package proof is missing")
        if header.get("recipient_key_id") != recipient.id:
            raise PackageError("result package was encrypted for another administrator")
        if expected_kind is not None and header.get("kind") != expected_kind:
            raise PackageError("unexpected package kind")
        if header.get("format_version") != FORMAT_VERSION:
            raise PackageError("unsupported package format version")

        aad = _canonical_json(header)
        expected_proof = hmac.new(session_auth_key, aad + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(proof_text), expected_proof):
            raise PackageError("result package does not belong to this session")

        try:
            ephemeral_public = X25519PublicKey.from_public_bytes(
                _unb64(str(header["ephemeral_public_key"]))
            )
            salt = _unb64(str(header["salt"]))
            nonce = _unb64(str(header["nonce"]))
            shared_secret = recipient.private_key.exchange(ephemeral_public)
            key = _derive_recipient_key(shared_secret, salt)
            payload = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except (InvalidTag, KeyError, ValueError) as exc:
            raise PackageError("result package is damaged") from exc
        return DecodedPackage(payload=payload, header=header)


def _derive_password_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise ValueError("package password must not be empty")
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))


def _derive_recipient_key(shared_secret: bytes, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"examdesk-result-v1",
    ).derive(shared_secret)


def _pack(magic: bytes, header: dict[str, Any], ciphertext: bytes) -> bytes:
    header_bytes = _canonical_json(header)
    if len(header_bytes) > MAX_HEADER_SIZE:
        raise PackageError("package header is too large")
    return magic + struct.pack(">I", len(header_bytes)) + header_bytes + ciphertext


def _unpack(expected_magic: bytes, package: bytes) -> tuple[dict[str, Any], bytes]:
    if len(package) < HEADER_PREFIX_SIZE or package[:8] != expected_magic:
        raise PackageError("invalid package file type")
    header_size = struct.unpack(">I", package[8:12])[0]
    if header_size <= 0 or header_size > MAX_HEADER_SIZE:
        raise PackageError("invalid package header size")
    header_end = HEADER_PREFIX_SIZE + header_size
    if header_end >= len(package):
        raise PackageError("package is incomplete")
    try:
        header = json.loads(package[HEADER_PREFIX_SIZE:header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError("package header is not valid JSON") from exc
    if not isinstance(header, dict):
        raise PackageError("package header must be an object")
    return header, package[header_end:]
