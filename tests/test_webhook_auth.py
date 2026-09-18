"""
Webhook signature verification.

This must fail closed: the service comments on and closes issues, so an
unsigned request that gets acted on is a real exposure. An unset secret
used to return True, which made the insecure path the default.
"""
import hashlib
import hmac

import pytest

from app import github


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ALLOW_UNSIGNED_WEBHOOKS", raising=False)


def _sign(secret: bytes, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()


def test_valid_signature_accepted(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    payload = b'{"action":"opened"}'

    assert github.verify_webhook_signature(
        payload, _sign(b"s3cret", payload)
    )


def test_wrong_signature_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")

    assert not github.verify_webhook_signature(b"{}", "sha256=deadbeef")


def test_signature_from_a_different_secret_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    payload = b'{"action":"opened"}'

    assert not github.verify_webhook_signature(
        payload, _sign(b"not-the-secret", payload)
    )


def test_tampered_payload_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    signature = _sign(b"s3cret", b'{"action":"opened"}')

    assert not github.verify_webhook_signature(
        b'{"action":"closed"}', signature
    )


def test_missing_secret_rejects_by_default():
    """The regression: an unset secret must not accept anything."""
    assert not github.verify_webhook_signature(b"{}", "sha256=deadbeef")


def test_missing_secret_rejects_even_a_plausible_signature():
    payload = b'{"action":"opened"}'
    assert not github.verify_webhook_signature(
        payload, _sign(b"guess", payload)
    )


def test_missing_signature_header_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")

    assert not github.verify_webhook_signature(b"{}", "")


def test_unsigned_allowed_only_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "1")

    assert github.verify_webhook_signature(b"{}", "")


def test_opt_in_requires_exactly_one(monkeypatch):
    """A truthy-looking value shouldn't silently disable verification."""
    for value in ["0", "true", "yes", "", "2"]:
        monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", value)
        assert not github.verify_webhook_signature(b"{}", "")


def test_secret_takes_precedence_over_opt_in(monkeypatch):
    """With a secret set, the opt-in must not weaken verification."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("ALLOW_UNSIGNED_WEBHOOKS", "1")

    assert not github.verify_webhook_signature(b"{}", "sha256=deadbeef")
