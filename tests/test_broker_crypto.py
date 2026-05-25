"""Tests for core.broker_crypto — AES-256-GCM credential encryption.

Run with: python3 -m unittest tests.test_broker_crypto -v

These tests don't touch the real BROKER_KEY_MASTER from .env; they generate
a throwaway key in each test setup so they're hermetic.
"""
from __future__ import annotations

import os
import secrets
import sys
import unittest
from pathlib import Path

# Add repo root to path so `from core.broker_crypto` works whether run from
# repo root or from tests/ directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from shared import broker_crypto  # noqa: E402
from shared.broker_crypto import (  # noqa: E402
    BrokerCryptoError,
    decrypt_credential,
    encrypt_credential,
)


class BrokerCryptoTestCase(unittest.TestCase):
    """Base — sets a fresh throwaway master key per test."""

    def setUp(self) -> None:
        self._saved_key = os.environ.get("BROKER_KEY_MASTER")
        os.environ["BROKER_KEY_MASTER"] = secrets.token_hex(32)
        broker_crypto._clear_master_key_cache()

    def tearDown(self) -> None:
        if self._saved_key is None:
            os.environ.pop("BROKER_KEY_MASTER", None)
        else:
            os.environ["BROKER_KEY_MASTER"] = self._saved_key
        broker_crypto._clear_master_key_cache()


class RoundTripTests(BrokerCryptoTestCase):

    def test_short_credential_round_trip(self):
        plain = "abc123"
        blob = encrypt_credential(plain)
        self.assertEqual(decrypt_credential(blob), plain)

    def test_long_credential_round_trip(self):
        # Oanda API secrets are 64-128 char base64 strings
        plain = "X" + secrets.token_urlsafe(96)
        blob = encrypt_credential(plain)
        self.assertEqual(decrypt_credential(blob), plain)

    def test_unicode_credential_round_trip(self):
        plain = "Test — Foundation · 中文 — 🔐"
        blob = encrypt_credential(plain)
        self.assertEqual(decrypt_credential(blob), plain)

    def test_encrypting_same_plaintext_twice_yields_different_ciphertext(self):
        """IV is random per call — same input must NEVER produce same output."""
        plain = "same input"
        a = encrypt_credential(plain)
        b = encrypt_credential(plain)
        self.assertNotEqual(a, b)
        # But both decrypt to the same plaintext
        self.assertEqual(decrypt_credential(a), plain)
        self.assertEqual(decrypt_credential(b), plain)

    def test_blob_is_valid_base64(self):
        import base64
        blob = encrypt_credential("test")
        # Round-trip through base64 must succeed (it's the storage format)
        decoded = base64.b64decode(blob, validate=True)
        # And contain at least an IV + tag (12 + 16 minimum)
        self.assertGreaterEqual(len(decoded), 28)


class TamperDetectionTests(BrokerCryptoTestCase):

    def test_tampered_ciphertext_rejected(self):
        """Modifying any byte of the ciphertext fails decryption."""
        import base64
        plain = "tamper-me"
        blob = encrypt_credential(plain)
        raw = bytearray(base64.b64decode(blob))
        # Flip the LAST byte (inside the GCM tag — flagrant tamper)
        raw[-1] ^= 0xFF
        tampered = base64.b64encode(bytes(raw)).decode("ascii")
        with self.assertRaises(BrokerCryptoError):
            decrypt_credential(tampered)

    def test_tampered_iv_rejected(self):
        import base64
        plain = "iv-tamper"
        blob = encrypt_credential(plain)
        raw = bytearray(base64.b64decode(blob))
        raw[0] ^= 0xFF  # flip a bit in the IV
        tampered = base64.b64encode(bytes(raw)).decode("ascii")
        with self.assertRaises(BrokerCryptoError):
            decrypt_credential(tampered)

    def test_blob_truncated_to_iv_only_rejected(self):
        import base64
        blob = encrypt_credential("xyz")
        raw = base64.b64decode(blob)
        # Keep just the first 12 bytes (IV); strip the ciphertext entirely
        truncated = base64.b64encode(raw[:12]).decode("ascii")
        with self.assertRaises(BrokerCryptoError):
            decrypt_credential(truncated)

    def test_completely_invalid_blob_rejected(self):
        with self.assertRaises(BrokerCryptoError):
            decrypt_credential("not-base64-!@#$%^")

    def test_empty_blob_rejected(self):
        with self.assertRaises(BrokerCryptoError):
            decrypt_credential("")


class WrongKeyTests(BrokerCryptoTestCase):

    def test_decryption_with_different_master_key_fails(self):
        plain = "wrong-key-test"
        blob = encrypt_credential(plain)

        # Swap the master key (simulating attempted decryption on a
        # different host with a different key)
        os.environ["BROKER_KEY_MASTER"] = secrets.token_hex(32)
        broker_crypto._clear_master_key_cache()

        with self.assertRaises(BrokerCryptoError):
            decrypt_credential(blob)

    def test_missing_master_key_raises(self):
        os.environ.pop("BROKER_KEY_MASTER", None)
        broker_crypto._clear_master_key_cache()
        with self.assertRaises(BrokerCryptoError) as ctx:
            encrypt_credential("anything")
        self.assertIn("BROKER_KEY_MASTER", str(ctx.exception))

    def test_malformed_master_key_too_short(self):
        os.environ["BROKER_KEY_MASTER"] = "abc"
        broker_crypto._clear_master_key_cache()
        with self.assertRaises(BrokerCryptoError):
            encrypt_credential("anything")

    def test_malformed_master_key_not_hex(self):
        os.environ["BROKER_KEY_MASTER"] = "z" * 64
        broker_crypto._clear_master_key_cache()
        with self.assertRaises(BrokerCryptoError):
            encrypt_credential("anything")

    def test_malformed_master_key_wrong_length(self):
        os.environ["BROKER_KEY_MASTER"] = "ab" * 30  # 60 chars instead of 64
        broker_crypto._clear_master_key_cache()
        with self.assertRaises(BrokerCryptoError):
            encrypt_credential("anything")


class InputValidationTests(BrokerCryptoTestCase):

    def test_empty_plaintext_rejected(self):
        with self.assertRaises(BrokerCryptoError):
            encrypt_credential("")

    def test_non_string_plaintext_rejected(self):
        with self.assertRaises(BrokerCryptoError):
            encrypt_credential(b"bytes")  # type: ignore[arg-type]
        with self.assertRaises(BrokerCryptoError):
            encrypt_credential(12345)  # type: ignore[arg-type]
        with self.assertRaises(BrokerCryptoError):
            encrypt_credential(None)  # type: ignore[arg-type]

    def test_non_string_blob_rejected(self):
        with self.assertRaises(BrokerCryptoError):
            decrypt_credential(b"bytes")  # type: ignore[arg-type]
        with self.assertRaises(BrokerCryptoError):
            decrypt_credential(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
