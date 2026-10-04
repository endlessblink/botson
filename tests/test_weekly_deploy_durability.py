"""Exercise the actual deploy script's snapshot/restore code offline."""

from pathlib import Path
import re
import sys

import yaml


def test_deploy_preserves_versioned_subscriptions_audit_and_current_code_defaults(monkeypatch, tmp_path):
    script = (Path(__file__).resolve().parents[1]/'scripts/deploy.sh').read_text()
    blocks = re.findall(r"\.venv/bin/python - \"\$WEEKLY_SNAPSHOT\" <<'PY'\n([\s\S]*?)\nPY", script)
    assert len(blocks) == 2
    directory = tmp_path/'config'
    directory.mkdir()
    settings = directory/'settings.yaml'
    snapshot = tmp_path/'private-snapshot.json'
    weekly = {'revision': 8, 'enabled': True, 'mode': 'auto_send',
              'selected_members': [{'user_id': 101, 'username': 'fixture_member'}],
              'excluded_user_ids': [303], 'audit': [{'revision': 8, 'action': 'opt_out'}]}
    settings.write_text(yaml.safe_dump({'weekly_state_review': weekly, 'code_default': 'old'}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['deploy-fixture', str(snapshot)])
    exec(compile(blocks[0], 'deploy-snapshot-fixture', 'exec'), {})
    # Simulate git reset updating versioned code/config defaults.
    settings.write_text(yaml.safe_dump({'weekly_state_review': {'revision': 0, 'enabled': False},
                                       'code_default': 'new'}))
    exec(compile(blocks[1], 'deploy-restore-fixture', 'exec'), {})
    restored = yaml.safe_load(settings.read_text())
    assert restored['weekly_state_review'] == weekly
    assert restored['code_default'] == 'new'


def test_unconfigured_public_template_does_not_override_new_code_defaults(monkeypatch, tmp_path):
    script = (Path(__file__).resolve().parents[1]/'scripts/deploy.sh').read_text()
    blocks = re.findall(r"\.venv/bin/python - \"\$WEEKLY_SNAPSHOT\" <<'PY'\n([\s\S]*?)\nPY", script)
    directory = tmp_path/'config'
    directory.mkdir()
    settings = directory/'settings.yaml'
    snapshot = tmp_path/'snapshot.json'
    settings.write_text(yaml.safe_dump({'weekly_state_review': {'enabled': False, 'revision': 0}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['fixture', str(snapshot)])
    exec(compile(blocks[0], 'snapshot-fixture', 'exec'), {})
    updated = {'weekly_state_review': {'enabled': False, 'revision': 0, 'mode': 'draft'}}
    settings.write_text(yaml.safe_dump(updated))
    exec(compile(blocks[1], 'restore-fixture', 'exec'), {})
    assert yaml.safe_load(settings.read_text()) == updated
