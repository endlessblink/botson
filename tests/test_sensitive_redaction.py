from bot.utils.redaction import redact_sensitive


def test_redacts_telegram_bot_token_in_url_and_plain_text():
    token = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    raw = f"POST https://api.telegram.org/bot{token}/sendMessage failed token={token}"

    redacted = redact_sensitive(raw)

    assert token not in redacted
    assert "[REDACTED_TELEGRAM_TOKEN]" in redacted


def test_redacts_codex_bearer_and_json_auth_tokens():
    raw = (
        'Authorization: Bearer sk-secret-value '
        '"access_token":"access-secret" '
        '"refresh_token": "refresh-secret" '
        'OPENAI_API_KEY=key-secret'
    )

    redacted = redact_sensitive(raw)

    for secret in ("sk-secret-value", "access-secret", "refresh-secret", "key-secret"):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") >= 4
