from app.agent import sanitized_ticket_fields
from app.privacy import redact_text


def test_redact_text_replaces_sensitive_values():
    result = redact_text("Email jane@example.com token=abcdef123456 from 10.0.0.7")

    assert "jane@example.com" not in result.sanitized_text
    assert "abcdef123456" not in result.sanitized_text
    assert "10.0.0.7" not in result.sanitized_text
    assert "[EMAIL_1]" in result.sanitized_text
    assert "[CREDENTIAL_1]" in result.sanitized_text
    assert "[IP_ADDRESS_1]" in result.sanitized_text
    assert len(result.findings) == 3


def test_sanitized_ticket_fields_prefers_combined_sanitized_text():
    short, description = sanitized_ticket_fields(
        "VPN issue",
        "Contact jane@example.com for details",
        "VPN issue\n\nContact [EMAIL_1] for details",
    )

    assert short == "VPN issue"
    assert description == "Contact [EMAIL_1] for details"
    assert "jane@example.com" not in description


def test_sanitized_ticket_fields_redacts_when_combined_text_missing():
    short, description = sanitized_ticket_fields(
        "jane@example.com cannot connect",
        "token=abcdef123456 is in logs",
        "",
    )

    assert "jane@example.com" not in short
    assert "abcdef123456" not in description
