import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import dashboard.app as dashboard_app


def test_dashboard_missing_password_fails_closed():
    with patch.object(dashboard_app, "DASHBOARD_PASSWORD", ""):
        try:
            asyncio.run(
                dashboard_app.login(
                    SimpleNamespace(session={}),
                    password="botson-admin",
                )
            )
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("missing dashboard password must reject login")


def test_repo_managed_units_do_not_embed_credentials():
    forbidden = ("BOT_TOKEN=", "DASHBOARD_PASSWORD=", "DASHBOARD_SECRET=", "CODEX_API_KEY=")
    for path in Path("systemd").glob("*"):
        if path.suffix not in {".service", ".timer", ".socket"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(fragment in text for fragment in forbidden), path
