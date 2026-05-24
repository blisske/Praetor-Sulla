"""Broker credential encryption — AES-256-GCM at rest.

Used to encrypt user-provided broker API keys (Oanda, eventually Oanda/Oanda)
before persisting to ``global.db``. Plaintext credentials never touch disk;
they live in engine container memory only during active broker API calls.

Storage format (returned by :func:`encrypt_credential`)::

    base64( iv (12 bytes) || ciphertext_with_tag (n + 16 bytes) )

A fresh random IV (96 bits, AES-GCM standard) is generated per call.
Re-encrypting the same plaintext yields a different ciphertext each time,
which is correct — IV must never repeat under the same key.

The master key lives in the host's ``~/swarm/ionic/.env`` as
``BROKER_KEY_MASTER`` (64 hex characters = 256 bits). Both the engine
container and the API container have this env var via the shared env_file.

See SAAS_BYOK_PLAN.md for the design rationale; see operator runbook for
master-key rotation procedure (if ever needed).
"""
from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ─── Public API ─────────────────────────────────────────────────────────────


class BrokerCryptoError(Exception):
    """Raised on any encryption / decryption failure, with a safe message.

    Callers should NOT include the exception's message in user-facing
    responses — it may hint at internal state (e.g., 'tag mismatch'
    indicating tampering). Log it server-side and return a generic
    'credential could not be decoded' to the user.
    """


def encrypt_credential(plaintext: str) -> str:
    """Encrypt a broker credential string.

    Args:
        plaintext: The API key, secret, or other sensitive string to
            encrypt. Must be a str; binary blobs aren't supported (the
            format is designed for short text credentials).

    Returns:
        Base64-encoded string suitable for storing in a SQLite TEXT
        column. The string contains the IV concatenated with the
        ciphertext-and-tag.

    Raises:
        BrokerCryptoError: If the master key is missing or malformed,
            or if the underlying AES-GCM operation fails.
    """
    if not isinstance(plaintext, str):
        raise BrokerCryptoError("plaintext must be a str")
    if not plaintext:
        raise BrokerCryptoError("plaintext cannot be empty")

    try:
        aesgcm = AESGCM(_master_key())
        iv = os.urandom(12)  # 96-bit nonce, GCM standard
        ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), associated_data=None)
        blob = iv + ciphertext
        return base64.b64encode(blob).decode("ascii")
    except BrokerCryptoError:
        raise
    except Exception as exc:  # pragma: no cover — defensive; AESGCM rarely raises on encrypt
        raise BrokerCryptoError(f"encryption failed: {type(exc).__name__}") from exc


def decrypt_credential(blob: str) -> str:
    """Decrypt a credential blob produced by :func:`encrypt_credential`.

    Args:
        blob: The base64-encoded ciphertext as stored in the DB.

    Returns:
        The original plaintext string.

    Raises:
        BrokerCryptoError: If the master key is wrong, the blob has been
            tampered with, or the format is invalid. The exception
            message does not distinguish between these cases by design
            (don't help attackers narrow down their attack surface).
    """
    if not isinstance(blob, str) or not blob:
        raise BrokerCryptoError("blob must be a non-empty str")

    try:
        raw = base64.b64decode(blob, validate=True)
    except Exception as exc:
        raise BrokerCryptoError("invalid base64 input") from exc

    if len(raw) < 12 + 16:  # IV (12) + minimum GCM tag (16)
        raise BrokerCryptoError("blob too short to be valid ciphertext")

    iv, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(_master_key())
    try:
        plaintext = aesgcm.decrypt(iv, ciphertext, associated_data=None)
    except InvalidTag as exc:
        # Could be: wrong master key, tampered ciphertext, malformed blob.
        # We don't disambiguate to the caller.
        raise BrokerCryptoError("decryption failed") from exc
    except Exception as exc:  # pragma: no cover
        raise BrokerCryptoError(f"decryption failed: {type(exc).__name__}") from exc

    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrokerCryptoError("decrypted bytes are not valid UTF-8") from exc


# ─── Internal helpers ───────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _master_key() -> bytes:
    """Load the master key from env, validate, and cache.

    The cache is process-local — if the env var changes (e.g., key
    rotation), the process must be restarted to pick up the new value.
    This is intentional: rotating the key requires re-encrypting every
    blob in the DB, so a clean restart is the only safe pivot point.
    """
    hex_key = os.environ.get("BROKER_KEY_MASTER")
    if not hex_key:
        raise BrokerCryptoError(
            "BROKER_KEY_MASTER not set in environment. "
            "Generate with `python3 -c 'import secrets; print(secrets.token_hex(32))'` "
            "and add to ~/swarm/ionic/.env. See SAAS_BYOK_PLAN.md."
        )
    hex_key = hex_key.strip()
    if len(hex_key) != 64:
        raise BrokerCryptoError(
            f"BROKER_KEY_MASTER must be exactly 64 hex chars (256-bit AES key); "
            f"got {len(hex_key)}"
        )
    try:
        key_bytes = bytes.fromhex(hex_key)
    except ValueError as exc:
        raise BrokerCryptoError("BROKER_KEY_MASTER is not valid hex") from exc
    if len(key_bytes) != 32:
        raise BrokerCryptoError(
            f"BROKER_KEY_MASTER decoded to {len(key_bytes)} bytes; expected 32"
        )
    return key_bytes


def _clear_master_key_cache() -> None:
    """Invalidate the cached master key. Used by tests; not for production code."""
    _master_key.cache_clear()
