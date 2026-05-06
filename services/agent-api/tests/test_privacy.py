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

