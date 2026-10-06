"""Telegram Mini App integrity and freshness, using the existing bot token."""
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qsl


def verify_init_data(raw, token, *, now, max_age):
    if not isinstance(raw, str) or len(raw) > 8192 or not token:
        raise ValueError('invalid_admission')
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(pairs) != len(fields):
        raise ValueError('duplicate_fields')
    signature = fields.pop('hash', '')
    if not re.fullmatch(r'[a-f0-9]{64}', signature):
        raise ValueError('invalid_signature')
    check = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError('invalid_signature')
    age = now - int(fields.get('auth_date', '0'))
    if not 0 <= age <= max_age:
        raise ValueError('expired_admission')
    user = json.loads(fields.get('user', '{}'))
    if not isinstance(user, dict) or type(user.get('id')) is not int or user['id'] < 1 or user.get('is_bot') is True:
        raise ValueError('invalid_player')
    return user['id']


def player_key(token, user_id):
    return hmac.new(token.encode(), ('arcade-player:'+str(user_id)).encode(), hashlib.sha256).hexdigest()
