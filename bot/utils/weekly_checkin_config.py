"""One versioned YAML configuration for dashboard, agent API and self-service.

The lock file contains no configuration. Every write re-reads settings under a
cross-process lock and atomically replaces that same settings.yaml file.
"""

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


class StaleWeeklyConfig(ValueError):
    pass


def configuration_digest(config):
    payload = {key: value for key, value in config.items() if key not in {'audit', 'revision'}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def next_weekly_occurrence(config, now):
    """Skip nonexistent DST times; use the first fold of an ambiguous time."""
    zone = ZoneInfo(config['timezone'])
    now = now.astimezone(zone)
    hour, minute = map(int, config['time'].split(':'))
    selected_day = config['days'][0]
    date = now.date() + timedelta(days=(selected_day - (now.weekday() + 1) % 7) % 7)
    while True:
        candidate = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone, fold=0)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
        if (roundtrip.replace(tzinfo=None) == candidate.replace(tzinfo=None) and candidate > now
                and (not config.get('not_before') or date.isoformat() >= config['not_before'])):
            return candidate
        date += timedelta(days=7)


def validate_timezone(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Select an explicit IANA timezone')
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError('Invalid IANA timezone') from None
    return value


def read_weekly_config(settings_path):
    settings = yaml.safe_load(Path(settings_path).read_text()) or {}
    return settings.get('weekly_state_review') or {}


def write_weekly_config(settings_path, *, expected_revision, candidate=None,
                        actor, action, event_key=None, member_change=None):
    """CAS for admin/agent updates; authenticated member changes merge in-lock.

    Returns (saved configuration, applied). Duplicate member events return the
    existing configuration without a second confirmation or revision.
    """
    path = Path(settings_path)
    with (path.parent / '.weekly-checkin.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = yaml.safe_load(path.read_text()) or {}
        previous = settings.get('weekly_state_review') or {}
        revision = previous.get('revision', 0)
        if type(revision) is not int or revision < 0:
            raise ValueError('Invalid weekly configuration revision')
        if expected_revision is not None and expected_revision != revision:
            raise StaleWeeklyConfig('Weekly configuration changed; read and preview again')
        audit = list(previous.get('audit') or [])
        if event_key and any(entry.get('event_key') == event_key for entry in audit):
            return previous, False
        if member_change is not None:
            saved = dict(previous)
            member, subscribe = member_change
            members = [item for item in previous.get('selected_members') or []
                       if item['user_id'] != member['user_id']]
            excluded = set(previous.get('excluded_user_ids') or [])
            if subscribe:
                members.append(member)
                excluded.discard(member['user_id'])
            else:
                excluded.add(member['user_id'])
            saved['selected_members'] = members
            saved['tag_usernames'] = [item['username'] for item in members if item.get('username')]
            saved['excluded_user_ids'] = sorted(excluded)
        else:
            saved = dict(candidate)
        saved['revision'] = revision + 1
        entry = {'revision': saved['revision'], 'at': datetime.now(timezone.utc).isoformat(),
                 'actor': str(actor), 'action': action, 'previous_digest': configuration_digest(previous),
                 'digest': configuration_digest(saved)}
        if event_key:
            entry['event_key'] = event_key
        saved['audit'] = [*audit, entry]
        settings['weekly_state_review'] = saved
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.weekly-settings-', delete=False) as output:
                temporary = output.name
                os.chmod(temporary, path.stat().st_mode & 0o777)
                yaml.safe_dump(settings, output, allow_unicode=True, sort_keys=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            temporary = None
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary:
                os.unlink(temporary)
        return saved, True
