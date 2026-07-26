"""Tests for core.email_sender — Postmark wrapper.

Mocks all HTTP calls. We're testing the request shape we send to
Postmark and the response interpretation, not Postmark itself.

Run with: docker exec -w /app ionic-api python3 -m unittest tests.test_email_sender -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import email_sender  # noqa: E402


class EmailSenderTestBase(unittest.TestCase):
    """Sets POSTMARK_SERVER_TOKEN to a fake value per test."""

    def setUp(self):
        self._saved = os.environ.get("POSTMARK_SERVER_TOKEN")
        os.environ["POSTMARK_SERVER_TOKEN"] = "test-token-not-real"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("POSTMARK_SERVER_TOKEN", None)
        else:
            os.environ["POSTMARK_SERVER_TOKEN"] = self._saved


def make_success_response(message_id: str = "abc-123") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "ErrorCode":   0,
        "Message":     "OK",
        "MessageID":   message_id,
        "SubmittedAt": "2026-05-22T15:00:00Z",
        "To":          "test@x.com",
    }
    return resp


def make_error_response(status_code: int, error_code: int, message: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "ErrorCode": error_code,
        "Message":   message,
    }
    return resp


# ─── send_email() core ─────────────────────────────────────────────────────


class SendEmailCoreTests(EmailSenderTestBase):

    def test_successful_send_returns_message_id(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response("msg-abc-123")
            result = email_sender.send_email(
                to="user@x.com",
                subject="Hi",
                text_body="hello",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["message_id"], "msg-abc-123")
        self.assertIsNotNone(result["submitted_at"])

    def test_request_body_includes_required_fields(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_email(
                to="user@x.com",
                subject="Test Subject",
                text_body="Test body",
                html_body="<p>Test HTML</p>",
                tag="test-tag",
            )
        called_with = mock_post.call_args
        body = called_with.kwargs["json"]
        self.assertEqual(body["To"], "user@x.com")
        self.assertEqual(body["Subject"], "Test Subject")
        self.assertEqual(body["TextBody"], "Test body")
        self.assertEqual(body["HtmlBody"], "<p>Test HTML</p>")
        self.assertEqual(body["Tag"], "test-tag")
        self.assertEqual(body["MessageStream"], "outbound")
        # From + ReplyTo come from module-level defaults
        self.assertIn("noreply@foundationbots.com", body["From"])
        self.assertIn("support@foundationbots.com", body["ReplyTo"])

    def test_request_includes_postmark_token_header(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_email("u@x.com", "subj", "body")
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Postmark-Server-Token"], "test-token-not-real")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_postmark_error_returned_as_ok_false(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_error_response(
                status_code=422,
                error_code=300,
                message="Invalid email request",
            )
            result = email_sender.send_email("u@x.com", "subj", "body")
        self.assertFalse(result["ok"])
        self.assertIn("Invalid email request", result["error"])
        self.assertEqual(result["error_code"], 300)

    def test_missing_token_raises_clean_error(self):
        os.environ.pop("POSTMARK_SERVER_TOKEN", None)
        result = email_sender.send_email("u@x.com", "subj", "body")
        self.assertFalse(result["ok"])
        self.assertIn("POSTMARK_SERVER_TOKEN", result["error"])

    def test_invalid_recipient_rejected_locally(self):
        """Empty/malformed recipient never hits the API."""
        with patch("core.email_sender.requests.post") as mock_post:
            for bad in ["", "not-an-email", None]:
                result = email_sender.send_email(bad, "subj", "body")  # type: ignore[arg-type]
                self.assertFalse(result["ok"])
            mock_post.assert_not_called()

    def test_timeout_returns_ok_false(self):
        import requests
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.side_effect = requests.Timeout()
            result = email_sender.send_email("u@x.com", "subj", "body")
        self.assertFalse(result["ok"])
        self.assertIn("timeout", result["error"].lower())

    def test_network_error_returns_ok_false(self):
        import requests
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("dns fail")
            result = email_sender.send_email("u@x.com", "subj", "body")
        self.assertFalse(result["ok"])
        self.assertIn("network", result["error"].lower())

    def test_non_json_response_returns_ok_false(self):
        resp = MagicMock()
        resp.status_code = 500
        resp.json.side_effect = ValueError("not json")
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = resp
            result = email_sender.send_email("u@x.com", "subj", "body")
        self.assertFalse(result["ok"])
        self.assertIn("non-json", result["error"].lower())


# ─── Template helpers ──────────────────────────────────────────────────────


class VerifyEmailTemplateTests(EmailSenderTestBase):

    def test_verify_email_substitutes_token(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_verify_email("u@x.com", "token-xyz")
        body = mock_post.call_args.kwargs["json"]
        self.assertIn("token=token-xyz", body["TextBody"])
        self.assertIn("token=token-xyz", body["HtmlBody"])
        self.assertIn("Confirm", body["Subject"])
        self.assertEqual(body["Tag"], "verify-email")

    def test_verify_uses_custom_base_url(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_verify_email("u@x.com", "tk", base_url="https://test.foo")
        body = mock_post.call_args.kwargs["json"]
        self.assertIn("https://test.foo/verify?token=tk", body["TextBody"])


class PasswordResetTemplateTests(EmailSenderTestBase):

    def test_password_reset_substitutes_token(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_password_reset_email("u@x.com", "tk-reset")
        body = mock_post.call_args.kwargs["json"]
        self.assertIn("token=tk-reset", body["TextBody"])
        self.assertIn("reset-password", body["TextBody"])
        self.assertIn("Reset", body["Subject"])
        self.assertEqual(body["Tag"], "password-reset")


class PasswordChangedNotificationTests(EmailSenderTestBase):

    def test_template_sent(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_password_changed_notification("u@x.com")
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["Tag"], "password-changed")
        self.assertIn("password was changed", body["Subject"].lower())
        # Should mention "wasn't you" warning
        self.assertIn("wasn't you", body["TextBody"].lower())


class EmailChangedNotificationTests(EmailSenderTestBase):

    def test_old_address_receives_notification(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_email_changed_notification("old@x.com", "new@x.com")
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["To"], "old@x.com")
        self.assertIn("new@x.com", body["TextBody"])
        self.assertEqual(body["Tag"], "email-changed")


class OandaRevokedTemplateTests(EmailSenderTestBase):

    def test_oanda_revoked_includes_error_message(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_oanda_key_revoked_email("u@x.com", "EAPI:Invalid key")
        body = mock_post.call_args.kwargs["json"]
        self.assertIn("EAPI:Invalid key", body["TextBody"])
        self.assertIn("shadow", body["TextBody"].lower())
        self.assertEqual(body["Tag"], "oanda-key-revoked")


class LiveModeActivatedTemplateTests(EmailSenderTestBase):

    def test_live_mode_template(self):
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response()
            email_sender.send_live_mode_activated_email("u@x.com")
        body = mock_post.call_args.kwargs["json"]
        self.assertIn("live mode", body["TextBody"].lower())
        self.assertIn("wasn't you", body["TextBody"].lower())
        self.assertEqual(body["Tag"], "live-mode-activated")


# ─── Undeliverable-recipient guard (swarm-ops #82) ─────────────────────────


class UndeliverableGuardTests(EmailSenderTestBase):
    """The shared foundation/shared/email_guard rules, wired at the chokepoint.

    A suppressed address must never reach Postmark, and must not look like an
    error to the caller — the old bughunt+ path returned ok=True for exactly
    that reason and the guard keeps the contract.
    """

    def test_reserved_domain_never_reaches_postmark(self):
        with patch("core.email_sender.requests.post") as mock_post:
            result = email_sender.send_email(
                "demo.counsel@keystone.example", "subj", "body")
            mock_post.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["message_id"], "suppressed")

    def test_test_harness_tags_never_reach_postmark(self):
        with patch("core.email_sender.requests.post") as mock_post:
            for bad in ["bughunt+seed@gmail.com", "hist+rk@gmail.com",
                        "x@example.com", "x@foo.invalid"]:
                result = email_sender.send_email(bad, "subj", "body")
                self.assertEqual(result["message_id"], "suppressed", bad)
            mock_post.assert_not_called()

    def test_legitimate_plus_tag_still_sends(self):
        """A real subscriber's +tag must NOT be collateral damage."""
        with patch("core.email_sender.requests.post") as mock_post:
            mock_post.return_value = make_success_response("msg-real")
            result = email_sender.send_email(
                "kevin+test@gmail.com", "subj", "body")
        mock_post.assert_called_once()
        self.assertEqual(result["message_id"], "msg-real")

if __name__ == "__main__":
    unittest.main(verbosity=2)
