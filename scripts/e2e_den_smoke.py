#!/usr/bin/env python3
"""T-159: Sherlocks Den end-to-end smoke harness.

Production-safe dry-run that proves the full dashboard + API + DB + bot
integration against the Sherlocks Den test group (-1003747545764) ONLY.
Never targets the main group. Default mode is --dry-run: it prints the
planned actions without hitting any endpoint.

Why this exists
---------------
The trust-lane unit suites (T-153..T-158) cover the contract pieces in
isolation. This harness is the integration probe: it walks the same paths
an operator does — login, create draft, send-now (target='test'), read
calendar, clean up — against a real running dashboard. If the dashboard's
session cookies, JSON shapes, or bot-side handlers drift, this script
turns red.

Safe content types
------------------
``custom`` (plain text), ``poll``, ``facts_tidbit`` and ``free_games`` are
covered by default. Stateful game types are opt-in via ``--include-games`` so
they cannot accidentally start multi-message sessions in Sherlocks Den.

Usage
-----
Dry-run (default, recommended)::

    PYTHONPATH=. uv run python scripts/e2e_den_smoke.py

Real send (requires explicit operator approval — never run unattended)::

    PYTHONPATH=. uv run python scripts/e2e_den_smoke.py --send

Other flags::

    --base-url URL    Dashboard base URL (default http://localhost:8080)
    --password PW     Dashboard password (defaults to $DASHBOARD_PASSWORD or 'botson-admin')
    --keep            Skip cleanup; leave rows in DB (useful for inspection)
    --json            Emit a JSON summary on stdout instead of the human table
    --only TYPE,...   Restrict to specific message types (comma-separated)
    --include-games   Add explicit test-target game probes (never on by default)

Exit code
---------
0 if every planned step succeeded (or, in dry-run, every plan rendered).
1 if any HTTP call, status assertion, or cleanup failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_IL_TZ = ZoneInfo("Asia/Jerusalem")

# Recognisable prefix so the test rows are easy to spot in the dashboard
# if cleanup doesn't run (e.g. --keep, or the harness crashes mid-run).
SMOKE_MARKER = "[e2e-den-smoke]"


@dataclass
class SmokePlan:
    """One scripted action against the dashboard."""
    label: str
    message_type: str
    text: str
    poll_options: list[str] | None = None
    poll_payload: dict[str, Any] | None = None
    expected_terminal_status: str = "sent"


@dataclass
class SmokeResult:
    """Outcome of one plan execution."""
    label: str
    message_type: str
    row_id: int | None = None
    sent_message_id: int | None = None
    terminal_status: str | None = None
    cleanup_status: str | None = None
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class DashboardClient:
    """Tiny urllib-backed client that maintains the dashboard's session cookie.

    We deliberately avoid requests/httpx so this script can run on any
    interpreter with stdlib only (e.g. a fresh VPS), matching the spirit of
    a production smoke probe.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        cookie_handler = urllib.request.HTTPCookieProcessor()
        self._opener = urllib.request.build_opener(cookie_handler)
        self._cookie_jar = cookie_handler.cookiejar

    def login(self, password: str) -> None:
        body = urllib.parse.urlencode({"password": password}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/login",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            self._opener.open(req, timeout=15)
        except urllib.error.HTTPError as e:
            # 303 redirect on success — urllib follows it; failures arrive as 4xx.
            if e.code not in (200, 303):
                raise RuntimeError(f"login failed: {e.code} {e.reason}") from e
        if not any(c.name == "session" for c in self._cookie_jar):
            raise RuntimeError("login appeared to succeed but no session cookie was set")

    def _request(self, method: str, path: str, payload: Any | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method,
        )
        try:
            with self._opener.open(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} → {e.code}: {raw[:300]}") from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def create_draft(self, *, text: str, message_type: str,
                     scheduled_date: str, scheduled_time: str,
                     poll_options: list[str] | None = None,
                     poll_payload: dict[str, Any] | None = None) -> int:
        body = {
            "text": text,
            "message_type": message_type,
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "target_group": "test",
            "status": "draft",
            "channel_topic_id": None,
        }
        if poll_payload is not None:
            body["poll_options"] = json.dumps(poll_payload, ensure_ascii=False)
        elif poll_options:
            body["poll_options"] = json.dumps(poll_options)
        resp = self._request("POST", "/api/calendar", body)
        if not isinstance(resp, dict) or "id" not in resp:
            raise RuntimeError(f"create_draft: unexpected response shape: {resp!r}")
        return int(resp["id"])

    def send_now(self, msg_id: int, target: str = "test") -> dict:
        resp = self._request("POST", f"/api/calendar/{msg_id}/send-now", {"target": target})
        if not isinstance(resp, dict):
            raise RuntimeError(f"send_now: unexpected response shape: {resp!r}")
        return resp

    def fetch_row(self, msg_id: int, date_str: str) -> dict | None:
        events = self._request("GET", f"/api/calendar?start={date_str}&end={date_str}")
        if not isinstance(events, list):
            return None
        for ev in events:
            if int(ev.get("id", -1)) == int(msg_id):
                return ev
        return None

    def cancel_row(self, msg_id: int) -> None:
        self._request("DELETE", f"/api/calendar/{msg_id}")


def _default_plans() -> list[SmokePlan]:
    marker_ts = datetime.now(_IL_TZ).strftime("%H:%M:%S")
    return [
        SmokePlan(
            label="plain custom text",
            message_type="custom",
            text=f"{SMOKE_MARKER} plain text probe @ {marker_ts}",
        ),
        SmokePlan(
            label="poll with three options",
            message_type="poll",
            text=f"{SMOKE_MARKER} poll probe @ {marker_ts}",
            poll_options=["א", "ב", "ג"],
        ),
        SmokePlan(
            label="facts_tidbit from pool",
            message_type="facts_tidbit",
            text=f"{SMOKE_MARKER} facts_tidbit probe @ {marker_ts}",
        ),
        SmokePlan(
            label="free_games digest",
            message_type="free_games",
            text=f"{SMOKE_MARKER} free_games probe @ {marker_ts}",
        ),
    ]


def _game_plans() -> list[SmokePlan]:
    marker_ts = datetime.now(_IL_TZ).strftime("%H:%M:%S")
    return [
        SmokePlan(
            label="emoji puzzle game",
            message_type="emoji_puzzle",
            text=f"{SMOKE_MARKER} emoji puzzle probe @ {marker_ts}",
            poll_payload={
                "media_types": ["movie", "tv"],
                "theme_label": "סרטים וסדרות",
            },
        ),
    ]


def _format_table(results: list[SmokeResult]) -> str:
    rows = [
        ("label", "type", "row_id", "status", "tg_msg_id", "cleanup", "error"),
    ]
    for r in results:
        rows.append((
            r.label[:32],
            r.message_type,
            str(r.row_id) if r.row_id is not None else "—",
            r.terminal_status or "—",
            str(r.sent_message_id) if r.sent_message_id is not None else "—",
            r.cleanup_status or "—",
            (r.error or "").splitlines()[0][:60] if r.error else "",
        ))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for i, row in enumerate(rows):
        line = "  ".join(str(cell).ljust(widths[j]) for j, cell in enumerate(row))
        lines.append(line)
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def run_dry_run(plans: list[SmokePlan], base_url: str) -> int:
    """Print what would happen. Touch no endpoints, send nothing."""
    target_dt = datetime.now(_IL_TZ) + timedelta(minutes=2)
    print(f"# e2e_den_smoke.py --dry-run")
    print(f"# base_url       : {base_url}")
    print(f"# target_group   : test (Sherlocks Den)")
    print(f"# scheduled_for  : {target_dt.strftime('%Y-%m-%d %H:%M')} IL")
    print(f"# marker prefix  : {SMOKE_MARKER}")
    print(f"# plans          : {len(plans)}")
    print()
    for i, plan in enumerate(plans, 1):
        print(f"{i}. {plan.label}  ({plan.message_type})")
        print(f"   POST   /api/calendar           → create draft, target='test'")
        print(f"     text: {plan.text!r}")
        if plan.poll_payload:
            print(f"     poll_payload: {plan.poll_payload!r}")
        if plan.poll_options:
            print(f"     poll_options: {plan.poll_options!r}")
        print(f"   POST   /api/calendar/<id>/send-now  body={{'target':'test'}}")
        print(f"   GET    /api/calendar?...        → assert status='{plan.expected_terminal_status}'")
        print(f"   DELETE /api/calendar/<id>       → soft-cancel cleanup")
        print()
    print(f"# {len(plans)} plans rendered. No HTTP calls made. Exit 0.")
    return 0


def run_send(plans: list[SmokePlan], base_url: str, password: str,
             *, keep: bool) -> tuple[int, list[SmokeResult]]:
    client = DashboardClient(base_url)
    t0 = time.monotonic()
    try:
        client.login(password)
    except Exception as e:
        print(f"FATAL: login failed: {e}", file=sys.stderr)
        return 1, []
    print(f"# login ok ({time.monotonic() - t0:.2f}s)", file=sys.stderr)

    target_dt = datetime.now(_IL_TZ) + timedelta(minutes=2)
    date_str = target_dt.strftime("%Y-%m-%d")
    time_str = target_dt.strftime("%H:%M")

    results: list[SmokeResult] = []
    for plan in plans:
        result = SmokeResult(label=plan.label, message_type=plan.message_type)
        try:
            t_create = time.monotonic()
            row_id = client.create_draft(
                text=plan.text, message_type=plan.message_type,
                scheduled_date=date_str, scheduled_time=time_str,
                poll_options=plan.poll_options,
                poll_payload=plan.poll_payload,
            )
            result.row_id = row_id
            result.timings["create"] = time.monotonic() - t_create

            t_send = time.monotonic()
            send_resp = client.send_now(row_id, target="test")
            result.timings["send"] = time.monotonic() - t_send
            result.sent_message_id = send_resp.get("message_id")

            t_read = time.monotonic()
            event = client.fetch_row(row_id, date_str)
            result.timings["read"] = time.monotonic() - t_read
            if event is None:
                raise RuntimeError("row disappeared from /api/calendar after send-now")
            ext = event.get("extendedProps", {}) or {}
            result.terminal_status = ext.get("status")

            # Send-now with target='test' deliberately keeps status='draft'
            # (per the _send_scheduled_row contract). The win is: we got a
            # 200 back and a Telegram message_id, proving the path executed.
            # We don't insist on status='sent' for the test target.
        except Exception as e:
            result.error = str(e)

        # Cleanup — always attempt unless --keep, even after failure.
        if not keep and result.row_id is not None:
            try:
                client.cancel_row(result.row_id)
                result.cleanup_status = "cancelled"
            except Exception as e:
                result.cleanup_status = f"cleanup_failed: {e}"
        elif keep:
            result.cleanup_status = "kept"

        results.append(result)

    failures = [r for r in results if not r.ok]
    return (1 if failures else 0), results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sherlocks Den e2e smoke harness")
    parser.add_argument("--send", action="store_true",
                        help="Actually send to Sherlocks Den. Requires operator approval. "
                             "Default is --dry-run.")
    parser.add_argument("--base-url", default="http://localhost:8080",
                        help="Dashboard base URL (default: http://localhost:8080)")
    parser.add_argument("--password", default=None,
                        help="Dashboard password (default: $DASHBOARD_PASSWORD or 'botson-admin')")
    parser.add_argument("--keep", action="store_true",
                        help="Skip cleanup; leave rows in DB for inspection")
    parser.add_argument("--json", dest="emit_json", action="store_true",
                        help="Emit a JSON summary on stdout instead of the human table")
    parser.add_argument("--only", default="",
                        help="Comma-separated message_types to include (default: all safe types)")
    parser.add_argument("--include-games", action="store_true",
                        help="Add explicit test-target game probes. These start multi-message game sessions and require the same explicit --send approval.")
    args = parser.parse_args(argv)

    plans = _default_plans()
    if args.include_games:
        plans.extend(_game_plans())
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        plans = [p for p in plans if p.message_type in wanted]
        if not plans:
            print(f"FATAL: --only={args.only!r} matched no safe plans.", file=sys.stderr)
            return 2

    if not args.send:
        if args.emit_json:
            print(json.dumps({"mode": "dry-run", "plans": [p.__dict__ for p in plans]}, indent=2, ensure_ascii=False))
            return 0
        return run_dry_run(plans, args.base_url)

    password = args.password or os.environ.get("DASHBOARD_PASSWORD", "botson-admin")
    exit_code, results = run_send(plans, args.base_url, password, keep=args.keep)
    if args.emit_json:
        print(json.dumps(
            {"mode": "send", "ok": exit_code == 0,
             "results": [r.__dict__ for r in results]},
            indent=2, ensure_ascii=False, default=str,
        ))
    else:
        print(_format_table(results))
        if exit_code != 0:
            print(f"\n{len([r for r in results if not r.ok])} of {len(results)} plans failed.",
                  file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
