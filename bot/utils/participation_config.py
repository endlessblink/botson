"""Shared versioned participation configuration; independent of weekly state."""
import fcntl
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml

KINDS = ('topic_news', 'occasional_replies')


def read_config(path):
    value = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(value, dict):
        raise ValueError('invalid configuration')
    return value


def validate_config(value, verified_topics):
    result = deepcopy(value)
    if set(result) - {'revision', 'audit', 'runtime', *KINDS}:
        raise ValueError('unknown configuration fields')
    for kind in KINDS:
        policy = result.get(kind)
        if not isinstance(policy, dict) or type(policy.get('enabled')) is not bool:
            raise ValueError('explicit feature state required')
        if policy.get('mode') not in {'preview_only', 'needs_decision', 'auto_send'}:
            raise ValueError('unsupported participation mode')
        if not policy['enabled']:
            continue
        if kind == 'topic_news':
            policy['topic_sources'] = {int(k): v for k, v in (policy.get('topic_sources') or {}).items()}
        ZoneInfo(policy['timezone'])
        for name in ('global_daily_cap', 'topic_daily_cap'):
            if type(policy.get(name)) is not int or policy[name] < 1:
                raise ValueError('positive daily caps required')
        for name in ('context_max_age_minutes',):
            if type(policy.get(name)) not in (int, float) or not math.isfinite(policy[name]) or policy[name] <= 0:
                raise ValueError('positive context freshness required')
        windows = policy.get('quiet_hours')
        if not isinstance(windows, list):
            raise ValueError('explicit quiet hours required')
        for window in windows:
            if not isinstance(window, list) or len(window) != 2:
                raise ValueError('invalid quiet hours')
            for at in window:
                if not isinstance(at, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', at):
                    raise ValueError('invalid time')
        topics = list((policy.get('topic_sources') or {}).keys()) if kind == 'topic_news' else policy.get('allowed_topics')
        if not topics or any(type(t) is not int or t not in verified_topics for t in topics):
            raise ValueError('select verified existing topics')
        score = policy.get('minimum_relevance' if kind == 'topic_news' else 'minimum_value')
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('explicit quality threshold required')
        if kind == 'topic_news':
            if policy.get('dedupe_scope') not in {'chat', 'topic'}:
                raise ValueError('explicit dedupe scope required')
            for name in ('freshness_hours', 'verification_max_age_hours'):
                if type(policy.get(name)) not in (int, float) or not math.isfinite(policy[name]) or policy[name] <= 0:
                    raise ValueError('positive article freshness required')
            times = policy.get('times')
            if not isinstance(times, list) or not times or len(set(times)) != len(times):
                raise ValueError('distinct ordinary schedule times required')
            for at in times:
                if not isinstance(at, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', at):
                    raise ValueError('invalid news schedule')
            sources = policy.get('sources') or {}
            for selected in policy['topic_sources'].values():
                if not isinstance(selected, list) or not selected or any(s not in sources for s in selected):
                    raise ValueError('selected topic needs a configured source')
            for source in sources.values():
                parts = urlsplit(source.get('feed_url') or '')
                if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
                    raise ValueError('HTTPS publisher feed required')
                if source.get('enabled') is not True or not source.get('allowed_domains') or not source.get('topic_label'):
                    raise ValueError('enabled publisher/domain/topic label required')
        else:
            if type(policy.get('allow_unsolicited')) is not bool or policy['allow_unsolicited']:
                raise ValueError('only explicit mentions/replies are supported')
            if type(policy.get('thread_daily_cap')) is not int or policy['thread_daily_cap'] < 1:
                raise ValueError('positive thread cap required')
            if policy.get('cooldown_scope') not in {'topic', 'conversation'} or type(policy.get('cooldown_minutes')) not in (int, float) or not math.isfinite(policy['cooldown_minutes']) or policy['cooldown_minutes'] <= 0:
                raise ValueError('explicit reply cooldown required')
    runtime = result.get('runtime') or {}
    if any(result[k]['enabled'] for k in KINDS):
        for name in ('context_messages', 'context_max_chars', 'max_reply_chars', 'max_news_chars',
                     'max_feed_bytes', 'max_articles_per_source', 'max_candidates_per_cycle', 'max_sends_per_cycle',
                     'fetch_timeout_seconds'):
            if type(runtime.get(name)) is not int or runtime[name] <= 0:
                raise ValueError('positive runtime budget required: '+name)
    return result


def save_config(path, value, *, expected_revision, verified_topics, actor):
    path = Path(path)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected revision required')
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_config(path)
        if current.get('revision', 0) != expected_revision:
            raise RuntimeError('configuration changed; reload before saving')
        candidate = validate_config(value, verified_topics)
        candidate['revision'] = expected_revision + 1
        candidate['audit'] = [*(current.get('audit') or []), {'actor': actor, 'revision': candidate['revision']}]
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as out:
                temporary = out.name
                os.chmod(temporary, path.stat().st_mode & 0o777)
                yaml.safe_dump(candidate, out, allow_unicode=True, sort_keys=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return candidate


def config_digest(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
