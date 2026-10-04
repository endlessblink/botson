"""Rendered weekly controls expose the shared validated configuration."""

import asyncio
from copy import deepcopy
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

from starlette.requests import Request

from bot.utils.config import get_settings
from dashboard import app as dash


def test_rendered_weekly_controls_do_not_invent_missing_day_time_or_topic(monkeypatch):
    settings = deepcopy(get_settings())
    settings['weekly_state_review'] = {'enabled': False, 'days': [], 'time': '', 'timezone': '',
                                      'topic_id': None, 'tag_usernames': [], 'mode': 'draft', 'revision': 7}
    monkeypatch.setattr(dash, 'get_settings', lambda: settings)
    request = Request({'type': 'http', 'method': 'GET', 'path': '/prompts', 'root_path': '',
                       'scheme': 'http', 'server': ('localhost', 8000), 'headers': [], 'query_string': b'',
                       'session': {'authenticated': True}, 'app': dash.app, 'router': dash.app.router})
    db = SimpleNamespace(get_forum_topics=AsyncMock(return_value=[{'topic_id': 99, 'name': 'Synthetic AI topic'}]))
    rendered = asyncio.run(dash.prompts_page(request, db)).body.decode()
    assert 'id="weekly-review-time" type="time" value=""' in rendered
    assert 'id="weekly-review-timezone" value=""' in rendered
    day = re.search(r'<select id="weekly-review-day"[\s\S]*?</select>', rendered).group()
    topic = re.search(r'<select id="weekly-review-topic"[\s\S]*?</select>', rendered).group()
    assert 'selected' not in day and 'selected' not in topic
    assert 'weeklyReviewRevision = 7' in rendered
    assert '/api/settings/weekly-state-review/preview' in rendered
    assert 'activation_approved' in rendered and 'preview_receipt' in rendered
    assert 'days: [6]' not in rendered


def test_weekly_and_review_template_scripts_have_valid_syntax(tmp_path):
    node = shutil.which('node')
    if not node:
        import pytest
        pytest.skip('Node syntax checker unavailable')
    directory = Path(__file__).resolve().parents[1] / 'dashboard/templates'
    for name in ('prompts.html', 'planner.html'):
        source = (directory/name).read_text()
        source = re.sub(r'{%[\s\S]*?%}', '', source)
        source = re.sub(r'{{\s*(?:[^{}]|{[^}]*})*\s*}}', '0', source)
        scripts = re.findall(r'<script[^>]*>([\s\S]*?)</script>', source)
        target = tmp_path/(name+'.js')
        target.write_text('\n;\n'.join(part for part in scripts if part.strip()))
        result = subprocess.run([node, '--check', str(target)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
