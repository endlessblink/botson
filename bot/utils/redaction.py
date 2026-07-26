"""Small, dependency-free redaction helpers for operator-visible diagnostics."""

from __future__ import annotations

import re


_TELEGRAM_TOKEN = re.compile(r"\d{8,12}:[A-Za-z0-9_-]{20,}")
_BEARER_TOKEN = re.compile(r"(?i)(\bauthorization\s*:\s*bearer\s+)[^\s,;]+")
_JSON_TOKEN = re.compile(
    r'(?i)(["\'](?:access_token|refresh_token|id_token|api_key)["\']\s*:\s*["\'])[^"\']+(["\'])'
)
_ASSIGNMENT_TOKEN = re.compile(
    r"(?i)(\b(?:OPENAI_API_KEY|CODEX_API_KEY|BOT_TOKEN|TELEGRAM_BOT_TOKEN|ACCESS_TOKEN|REFRESH_TOKEN)\s*=\s*)[^\s,;]+"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b")


def redact_sensitive(value: object) -> str:
    """Redact common Telegram and Codex/OpenAI credential forms."""
    text = str(value or "")
    text = _TELEGRAM_TOKEN.sub("[REDACTED_TELEGRAM_TOKEN]", text)
    text = _BEARER_TOKEN.sub(r"\1[REDACTED]", text)
    text = _JSON_TOKEN.sub(r"\1[REDACTED]\2", text)
    text = _ASSIGNMENT_TOKEN.sub(r"\1[REDACTED]", text)
    return _JWT.sub("[REDACTED]", text)
