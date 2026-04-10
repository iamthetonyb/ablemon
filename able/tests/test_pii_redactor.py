"""Tests for able.core.security.pii_redactor — PII detection and redaction."""

import pytest
from able.core.security.pii_redactor import redact_pii, has_pii


# ── Email ─────────────────────────────────────────────────────────

def test_email_redacted():
    text = "Contact me at john@example.com for details."
    result, redactions = redact_pii(text)
    assert "john@example.com" not in result
    assert "[REDACTED_EMAIL_" in result
    assert len(redactions) == 1
    assert redactions[0].field_type == "email"


def test_multiple_emails():
    text = "From alice@corp.com to bob@corp.com"
    result, redactions = redact_pii(text)
    assert "alice@corp.com" not in result
    assert "bob@corp.com" not in result
    assert len([r for r in redactions if r.field_type == "email"]) == 2


# ── Phone ─────────────────────────────────────────────────────────

def test_phone_us_format():
    text = "Call me at (555) 123-4567"
    result, redactions = redact_pii(text)
    assert "(555) 123-4567" not in result
    assert any(r.field_type == "phone" for r in redactions)


def test_phone_with_country_code():
    text = "My number is +1-555-123-4567"
    result, redactions = redact_pii(text)
    assert "555-123-4567" not in result


# ── SSN ───────────────────────────────────────────────────────────

def test_ssn_redacted():
    text = "SSN: 123-45-6789"
    result, redactions = redact_pii(text)
    assert "123-45-6789" not in result
    assert any(r.field_type == "ssn" for r in redactions)


def test_ssn_with_spaces():
    text = "SSN: 123 45 6789"
    result, redactions = redact_pii(text)
    assert "123 45 6789" not in result


# ── Credit card ───────────────────────────────────────────────────

def test_credit_card_redacted():
    text = "Card: 4111-1111-1111-1111"
    result, redactions = redact_pii(text)
    assert "4111-1111-1111-1111" not in result
    assert any(r.field_type == "credit_card" for r in redactions)


# ── API keys ──────────────────────────────────────────────────────

def test_openai_key_redacted():
    text = "key=sk-abcdefghijklmnopqrstuvwxyz1234567890"
    result, redactions = redact_pii(text)
    assert "sk-abcdefghijklmnopqrst" not in result
    assert any(r.field_type == "api_key" for r in redactions)


def test_github_pat_redacted():
    text = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
    result, redactions = redact_pii(text)
    assert "ghp_ABCDEF" not in result


def test_aws_key_redacted():
    text = "AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE"
    result, redactions = redact_pii(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in result


# ── No PII ────────────────────────────────────────────────────────

def test_no_pii_unchanged():
    text = "Hello world, this is a normal message about coding."
    result, redactions = redact_pii(text)
    assert result == text
    assert len(redactions) == 0


def test_has_pii_true():
    assert has_pii("Contact john@example.com")


def test_has_pii_false():
    assert not has_pii("Normal text with no PII")


# ── Mixed PII ────────────────────────────────────────────────────

def test_mixed_pii_all_redacted():
    text = "Email: user@test.com, Phone: 555-123-4567, SSN: 123-45-6789"
    result, redactions = redact_pii(text)
    assert "user@test.com" not in result
    assert "555-123-4567" not in result
    assert "123-45-6789" not in result
    assert len(redactions) >= 3
