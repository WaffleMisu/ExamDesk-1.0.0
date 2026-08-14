from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from examdesk.packages import SigningKeyPair, X25519KeyPair

from .windows_dpapi import protect_for_current_user, unprotect_for_current_user

KEY_VAULT_SETTING = "organization_key_vault"
TRUSTED_SIGNERS_SETTING = "trusted_signers"
TRUST_MAGIC = "ExamDeskTrust"


class OrganizationKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OrganizationKeys:
    signing: SigningKeyPair
    result_recipient: X25519KeyPair


class OrganizationKeyStore:
    def __init__(self, database) -> None:
        self.database = database

    def ensure_initialized(self) -> OrganizationKeys:
        try:
            return self.load()
        except KeyError:
            pass
        keys = OrganizationKeys(SigningKeyPair.generate(), X25519KeyPair.generate())
        created_at = datetime.now(UTC).isoformat()
        private_payload = json.dumps(
            {
                "schema_version": 1,
                "signing_private": _b64(keys.signing.private_bytes()),
                "recipient_private": _b64(keys.result_recipient.private_bytes()),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        setting = {
            "schema_version": 1,
            "created_at": created_at,
            "signer_id": keys.signing.id,
            "signing_public": _b64(keys.signing.public_bytes),
            "recipient_id": keys.result_recipient.id,
            "recipient_public": _b64(keys.result_recipient.public_bytes),
            "protected_private": _b64(protect_for_current_user(private_payload)),
        }
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (KEY_VAULT_SETTING,),
            ).fetchone()
            if existing is not None:
                return self.load()
            connection.execute(
                "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                (KEY_VAULT_SETTING, _json(setting), created_at),
            )
        self._trust_public_key(keys.signing.id, keys.signing.public_bytes, "本机组织密钥")
        return keys

    def load(self) -> OrganizationKeys:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (KEY_VAULT_SETTING,),
            ).fetchone()
        if row is None:
            raise KeyError(KEY_VAULT_SETTING)
        try:
            setting = json.loads(row["value_json"])
            private_payload = json.loads(
                unprotect_for_current_user(_unb64(setting["protected_private"])).decode()
            )
            keys = OrganizationKeys(
                SigningKeyPair.from_private_bytes(_unb64(private_payload["signing_private"])),
                X25519KeyPair.from_private_bytes(_unb64(private_payload["recipient_private"])),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OrganizationKeyError("组织密钥损坏或不属于当前Windows用户") from exc
        if keys.signing.id != setting.get("signer_id"):
            raise OrganizationKeyError("组织签名密钥校验失败")
        if keys.result_recipient.id != setting.get("recipient_id"):
            raise OrganizationKeyError("收卷解密密钥校验失败")
        return keys

    def export_portable_keys(self) -> dict[str, str]:
        keys = self.load()
        return {
            "schema_version": 1,
            "signer_id": keys.signing.id,
            "recipient_id": keys.result_recipient.id,
            "signing_private": _b64(keys.signing.private_bytes()),
            "recipient_private": _b64(keys.result_recipient.private_bytes()),
        }

    def rebind_portable_keys(self, payload: dict) -> OrganizationKeys:
        try:
            if int(payload["schema_version"]) != 1:
                raise ValueError("unsupported portable key version")
            keys = OrganizationKeys(
                SigningKeyPair.from_private_bytes(_unb64(payload["signing_private"])),
                X25519KeyPair.from_private_bytes(_unb64(payload["recipient_private"])),
            )
            if keys.signing.id != str(payload["signer_id"]):
                raise ValueError("signing key id does not match")
            if keys.result_recipient.id != str(payload["recipient_id"]):
                raise ValueError("recipient key id does not match")
        except (KeyError, TypeError, ValueError) as exc:
            raise OrganizationKeyError("备份中的组织私钥无效") from exc

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (KEY_VAULT_SETTING,),
            ).fetchone()
        if row is None:
            raise OrganizationKeyError("备份中缺少组织密钥设置")
        try:
            setting = json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise OrganizationKeyError("备份中的组织密钥设置损坏") from exc
        if setting.get("signer_id") != keys.signing.id:
            raise OrganizationKeyError("备份签名密钥与数据库不一致")
        if setting.get("recipient_id") != keys.result_recipient.id:
            raise OrganizationKeyError("备份收卷密钥与数据库不一致")

        private_payload = json.dumps(
            {
                "schema_version": 1,
                "signing_private": _b64(keys.signing.private_bytes()),
                "recipient_private": _b64(keys.result_recipient.private_bytes()),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        setting["protected_private"] = _b64(protect_for_current_user(private_payload))
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_settings SET value_json = ?, updated_at = ? WHERE key = ?",
                (_json(setting), now, KEY_VAULT_SETTING),
            )
        self._trust_public_key(keys.signing.id, keys.signing.public_bytes, "恢复的组织密钥")
        return keys

    def export_trust_certificate(self) -> bytes:
        keys = self.load()
        payload = {
            "magic": TRUST_MAGIC,
            "schema_version": 1,
            "signer_id": keys.signing.id,
            "signing_public": _b64(keys.signing.public_bytes),
        }
        payload["fingerprint"] = _fingerprint(keys.signing.public_bytes)
        return (_json(payload) + "\n").encode("utf-8")

    def import_trust_certificate(self, data: bytes, source_name: str = "导入证书") -> str:
        signer_id, public_key, public_bytes = self.parse_trust_certificate(data)
        self._trust_public_key(signer_id, public_bytes, source_name)
        return signer_id

    @staticmethod
    def parse_trust_certificate(
        data: bytes,
    ) -> tuple[str, Ed25519PublicKey, bytes]:
        try:
            payload = json.loads(data.decode("utf-8"))
            public_bytes = _unb64(payload["signing_public"])
            public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OrganizationKeyError("信任证书格式无效") from exc
        if payload.get("magic") != TRUST_MAGIC or payload.get("schema_version") != 1:
            raise OrganizationKeyError("信任证书版本不受支持")
        signer_id = str(payload.get("signer_id", ""))
        if signer_id != hashlib.sha256(public_bytes).hexdigest()[:24]:
            raise OrganizationKeyError("信任证书公钥编号不一致")
        if payload.get("fingerprint") != _fingerprint(public_bytes):
            raise OrganizationKeyError("信任证书指纹校验失败")
        return signer_id, public_key, public_bytes

    def trusted_public_keys(self) -> dict[str, Ed25519PublicKey]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (TRUSTED_SIGNERS_SETTING,),
            ).fetchone()
        if row is None:
            return {}
        try:
            values = json.loads(row["value_json"])
            return {
                signer_id: Ed25519PublicKey.from_public_bytes(_unb64(item["public_key"]))
                for signer_id, item in values.items()
            }
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OrganizationKeyError("本机可信签名列表损坏") from exc

    def _trust_public_key(self, signer_id: str, public_bytes: bytes, source_name: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (TRUSTED_SIGNERS_SETTING,),
            ).fetchone()
            values = json.loads(row["value_json"]) if row is not None else {}
            existing = values.get(signer_id)
            encoded = _b64(public_bytes)
            if existing is not None and existing.get("public_key") != encoded:
                raise OrganizationKeyError("可信签名编号发生冲突")
            values[signer_id] = {
                "public_key": encoded,
                "source": source_name,
                "trusted_at": existing.get("trusted_at", now) if existing else now,
            }
            if row is None:
                connection.execute(
                    "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                    (TRUSTED_SIGNERS_SETTING, _json(values), now),
                )
            else:
                connection.execute(
                    "UPDATE app_settings SET value_json = ?, updated_at = ? WHERE key = ?",
                    (_json(values), now, TRUSTED_SIGNERS_SETTING),
                )


def _fingerprint(public_bytes: bytes) -> str:
    digest = hashlib.sha256(public_bytes).hexdigest().upper()
    return "-".join(digest[index : index + 4] for index in range(0, 32, 4))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
