"""Cover upload accepts the dashboard session or the scoped agent token, nothing else."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dashboard import app as dash

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _Upload:
    content_type = "image/png"

    async def read(self, _n):
        return PNG


def _request(token="", session=None):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}" if token else ""},
        session=session or {},
    )


def test_anonymous_upload_is_refused(monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.upload_cover(_request(token="wrong"), _Upload()))
    assert error.value.status_code == 401


def test_agent_token_passes_the_auth_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    monkeypatch.setattr(dash, "_validated_cover_ext", lambda *_a: "png")
    monkeypatch.setattr(dash, "COVERS_DIR", tmp_path)
    monkeypatch.setattr(dash, "MEDIA_DIR", tmp_path.parent)
    result = asyncio.run(dash.upload_cover(_request(token="agent-secret-value"), _Upload()))
    assert result["status"] == "ok"
    assert len(list(tmp_path.iterdir())) == 1
