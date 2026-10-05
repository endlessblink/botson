"""Read configured HTTPS publisher RSS; never accept client verification claims."""
import asyncio
import calendar
import hashlib
import html
import ipaddress
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

import feedparser
import httpx

from .community_participation import _canonical_url


async def public_host(host):
    records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not records or any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
        raise ValueError('publisher address is not public')


async def fetch_articles(source_id, source, runtime, *, now, client=None):
    """Publication dates and canonical article links come from the fetched feed.

    No redirects, credentials, article HTML or broad crawling. Missing dates,
    wrong domains and malformed feeds produce no eligible news.
    """
    parts = urlsplit(source['feed_url'])
    if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('invalid publisher feed')
    await public_host(parts.hostname)
    owned = client is None
    client = client or httpx.AsyncClient(timeout=runtime['fetch_timeout_seconds'], follow_redirects=False)
    try:
        async with client.stream('GET', source['feed_url'], follow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('publisher feed did not return 200')
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > runtime['max_feed_bytes']:
                    raise ValueError('publisher feed exceeds configured budget')
        feed = feedparser.parse(bytes(content))
        if not feed.entries:
            return []
        results = []
        domains = {d.lower().rstrip('.') for d in source['allowed_domains']}
        for entry in feed.entries:
            try:
                url = _canonical_url(entry.get('link'))
                if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in domains:
                    continue
                parsed = entry.get('published_parsed') or entry.get('updated_parsed')
                if not parsed:
                    continue
                published = datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc)
                if published > now:
                    continue
                title = html.unescape(re.sub(r'<[^>]*>', '', entry.get('title') or '')).strip()
                body = html.unescape(re.sub(r'<[^>]*>', '', entry.get('summary') or '')).strip()
                if not title or not body:
                    continue
                results.append({'source_id': source_id, 'source_url': url, 'published_at': published.isoformat(),
                    'verified_at': now.isoformat(), 'source_verified': True, 'title': title,
                    'source_excerpt': body[:runtime['context_max_chars']],
                    'event_id': 'article:'+hashlib.sha256(url.encode()).hexdigest()})
                if len(results) >= runtime['max_articles_per_source']:
                    break
            except (ValueError, TypeError, OverflowError):
                continue
        return results
    finally:
        if owned:
            await client.aclose()
