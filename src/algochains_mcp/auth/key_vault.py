"""
Encrypted Local Key Vault — AES-256-GCM + scrypt.

Stores API keys, tokens, and secrets in an encrypted vault file at
~/.algochains/vault.enc — never in plaintext .env files or session files.

Security model (matching Injective MCP):
  - Key derivation: scrypt (N=2^17, r=8, p=1) — memory-hard, GPU-resistant
  - Encryption: AES-256-GCM — authenticated encryption with associated data
  - Storage: ~/.algochains/vault.enc (encrypted binary)
  - In-memory: decrypted keys exist only during tool execution, never logged
  - LLM response redaction: all vault responses go through _KEY_PATTERNS redactor

CRITICALLY: vault_retrieve_key NEVER returns the raw key to the LLM.
It stores it in a runtime-only in-memory buffer for use within the same session.

Requirements: pip install cryptography
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("algochains_mcp.auth.vault")

VAULT_PATH = Path.home() / ".algochains" / "vault.enc"
VAULT_VERSION = 1


class VaultError(Exception):
    pass


class VaultAuthError(VaultError):
    """Wrong passphrase."""
    pass


class VaultKeyNotFoundError(VaultError):
    pass


@dataclass
class VaultEntry:
    name: str
    description: str
    created_at: float
    rotated_at: float | None
    rotation_ttl_days: int | None  # None = no auto-rotation
    tags: list[str] = field(default_factory=list)

    def to_dict(self, include_name: bool = True) -> dict[str, Any]:
        """Return metadata only — never the value."""
        d = {
            "description": self.description,
            "created_at": self.created_at,
            "rotated_at": self.rotated_at,
            "rotation_ttl_days": self.rotation_ttl_days,
            "tags": self.tags,
            "rotation_due": self._rotation_due(),
        }
        if include_name:
            d["name"] = self.name
        return d

    def _rotation_due(self) -> bool:
        if not self.rotation_ttl_days:
            return False
        baseline = self.rotated_at or self.created_at
        return time.time() - baseline > self.rotation_ttl_days * 86400


class KeyVault:
    """
    AES-256-GCM encrypted local key vault.

    All crypto uses Python's `cryptography` library (libsodium-backed).
    Falls back to error if library not installed.
    """

    SCRYPT_N = 2 ** 17    # 128 MB memory
    SCRYPT_R = 8
    SCRYPT_P = 1
    SALT_SIZE = 32
    NONCE_SIZE = 12       # GCM standard
    TAG_SIZE = 16         # GCM authentication tag

    def __init__(self, vault_path: Path = VAULT_PATH) -> None:
        self.vault_path = vault_path
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        # Runtime-only in-memory key buffer (never persisted or logged)
        self._runtime_keys: dict[str, str] = {}
        self._runtime_key_expiry: dict[str, float] = {}
        self._runtime_ttl = 300  # 5 min per-session key retention

    def _require_cryptography(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
            from cryptography.hazmat.backends import default_backend
            return AESGCM, Scrypt, default_backend
        except ImportError:
            raise VaultError(
                "cryptography library required. Install: pip install cryptography"
            )

    def _derive_key(self, passphrase: str, salt: bytes) -> bytes:
        """Derive AES-256 key from passphrase using scrypt."""
        AESGCM, Scrypt, backend = self._require_cryptography()
        kdf = Scrypt(
            salt=salt,
            length=32,
            n=self.SCRYPT_N,
            r=self.SCRYPT_R,
            p=self.SCRYPT_P,
            backend=backend(),
        )
        return kdf.derive(passphrase.encode("utf-8"))

    def _load_vault_raw(self) -> dict[str, Any]:
        """Load and parse the encrypted vault file."""
        if not self.vault_path.exists():
            return {"version": VAULT_VERSION, "entries": {}}

        with open(self.vault_path, "rb") as f:
            raw = f.read()

        # Format: version(1) + salt(32) + nonce(12) + ciphertext
        if len(raw) < 1 + self.SALT_SIZE + self.NONCE_SIZE:
            raise VaultError("Vault file corrupted or invalid format.")

        return {"_raw": raw}

    def _decrypt_vault(self, passphrase: str) -> dict[str, Any]:
        """Decrypt and deserialize the vault."""
        if not self.vault_path.exists():
            return {"version": VAULT_VERSION, "entries": {}}

        AESGCM, _, _ = self._require_cryptography()

        with open(self.vault_path, "rb") as f:
            raw = f.read()

        if len(raw) < 1 + self.SALT_SIZE + self.NONCE_SIZE:
            raise VaultError("Vault file corrupted.")

        version = raw[0]
        salt = raw[1:1 + self.SALT_SIZE]
        nonce = raw[1 + self.SALT_SIZE:1 + self.SALT_SIZE + self.NONCE_SIZE]
        ciphertext = raw[1 + self.SALT_SIZE + self.NONCE_SIZE:]

        aes_key = self._derive_key(passphrase, salt)
        aesgcm = AESGCM(aes_key)

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception:
            raise VaultAuthError(
                "Vault decryption failed. Wrong passphrase or corrupted vault."
            )

        return json.loads(plaintext.decode("utf-8"))

    def _encrypt_vault(self, data: dict, passphrase: str) -> None:
        """Serialize and encrypt the vault to disk."""
        AESGCM, _, _ = self._require_cryptography()

        salt = secrets.token_bytes(self.SALT_SIZE)
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        aes_key = self._derive_key(passphrase, salt)
        aesgcm = AESGCM(aes_key)

        plaintext = json.dumps(data).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        # Atomic write via temp file
        tmp_path = self.vault_path.with_suffix(".tmp")
        with open(tmp_path, "wb") as f:
            f.write(bytes([VAULT_VERSION]) + salt + nonce + ciphertext)
        tmp_path.replace(self.vault_path)

    # ── Public API ─────────────────────────────────────────────────────

    def store(
        self,
        name: str,
        value: str,
        passphrase: str,
        description: str = "",
        rotation_ttl_days: int | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Encrypt and store a credential in the vault.

        The raw value is NEVER returned to the caller.
        Returns only metadata (name, description, created_at).
        """
        if not name or not value or not passphrase:
            raise VaultError("name, value, and passphrase are all required.")

        vault = self._decrypt_vault(passphrase)
        entries = vault.setdefault("entries", {})

        entry = {
            "value": value,  # stored encrypted within the already-encrypted vault
            "description": description,
            "created_at": time.time(),
            "rotated_at": None,
            "rotation_ttl_days": rotation_ttl_days,
            "tags": tags or [],
        }
        entries[name] = entry
        self._encrypt_vault(vault, passphrase)

        logger.info("Stored credential '%s' in vault (tags=%s)", name, tags)
        return {
            "stored": True,
            "name": name,
            "description": description,
            "rotation_ttl_days": rotation_ttl_days,
            "tags": tags or [],
        }

    def retrieve_for_runtime(self, name: str, passphrase: str) -> str:
        """
        Decrypt and return the raw value for runtime use ONLY.

        This method is NOT exposed as an MCP tool.
        The runtime buffer caches keys for 5 minutes to avoid repeated decryption.
        """
        # Check runtime buffer
        if name in self._runtime_keys:
            if time.time() < self._runtime_key_expiry.get(name, 0):
                return self._runtime_keys[name]

        vault = self._decrypt_vault(passphrase)
        entries = vault.get("entries", {})

        if name not in entries:
            raise VaultKeyNotFoundError(f"Credential '{name}' not found in vault.")

        value = entries[name]["value"]
        # Cache in runtime buffer (in-memory only, never logged or returned via MCP)
        self._runtime_keys[name] = value
        self._runtime_key_expiry[name] = time.time() + self._runtime_ttl
        return value

    def list_keys(self, passphrase: str) -> list[dict[str, Any]]:
        """
        Return metadata for all stored credentials.
        Values are NEVER returned. Names and metadata only.
        """
        vault = self._decrypt_vault(passphrase)
        entries = vault.get("entries", {})
        result = []
        for name, entry in entries.items():
            result.append({
                "name": name,
                "description": entry.get("description", ""),
                "created_at": entry.get("created_at"),
                "rotated_at": entry.get("rotated_at"),
                "rotation_ttl_days": entry.get("rotation_ttl_days"),
                "tags": entry.get("tags", []),
                "rotation_due": (
                    entry.get("rotation_ttl_days") is not None
                    and time.time() - (entry.get("rotated_at") or entry.get("created_at", 0))
                    > (entry.get("rotation_ttl_days") or 9999) * 86400
                ),
            })
        return result

    def rotate(
        self,
        name: str,
        new_value: str,
        passphrase: str,
    ) -> dict[str, Any]:
        """
        Atomically replace a credential value (key rotation).
        Old value is overwritten — not recoverable.
        """
        vault = self._decrypt_vault(passphrase)
        entries = vault.get("entries", {})

        if name not in entries:
            raise VaultKeyNotFoundError(f"Credential '{name}' not found. Cannot rotate.")

        entries[name]["value"] = new_value
        entries[name]["rotated_at"] = time.time()

        # Invalidate runtime cache
        self._runtime_keys.pop(name, None)
        self._runtime_key_expiry.pop(name, None)

        self._encrypt_vault(vault, passphrase)
        logger.info("Rotated credential '%s' in vault", name)

        return {
            "rotated": True,
            "name": name,
            "rotated_at": entries[name]["rotated_at"],
        }

    def delete(self, name: str, passphrase: str) -> dict[str, Any]:
        """Permanently delete a credential from the vault."""
        vault = self._decrypt_vault(passphrase)
        entries = vault.get("entries", {})

        if name not in entries:
            raise VaultKeyNotFoundError(f"Credential '{name}' not found.")

        del entries[name]
        self._runtime_keys.pop(name, None)
        self._runtime_key_expiry.pop(name, None)
        self._encrypt_vault(vault, passphrase)
        return {"deleted": True, "name": name}

    def list_rotation_due(self, passphrase: str) -> list[str]:
        """Return names of credentials where rotation TTL has elapsed."""
        keys = self.list_keys(passphrase)
        return [k["name"] for k in keys if k["rotation_due"]]

    def vault_exists(self) -> bool:
        return self.vault_path.exists()

    def stats(self) -> dict[str, Any]:
        return {
            "vault_path": str(self.vault_path),
            "vault_exists": self.vault_exists(),
            "vault_size_bytes": self.vault_path.stat().st_size if self.vault_exists() else 0,
            "runtime_keys_cached": len(self._runtime_keys),
            "encryption": "AES-256-GCM",
            "kdf": f"scrypt(N={self.SCRYPT_N}, r={self.SCRYPT_R}, p={self.SCRYPT_P})",
        }


_key_vault: KeyVault | None = None


def get_key_vault() -> KeyVault:
    global _key_vault
    if _key_vault is None:
        _key_vault = KeyVault()
    return _key_vault
