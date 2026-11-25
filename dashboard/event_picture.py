"""Read a picture of an event into draft form fields (Codex CLI, then Claude CLI).

Everything that comes back is untrusted text from a stranger's picture: strict JSON,
known keys only, each field through the form's own checks, a bad field is dropped. The
AI gets no tools, so text inside the picture can at most become a wrong draft that a
person still reviews and submits. The picture lives in a temp folder only while it is
being read, and only field names and lengths are logged.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path

from bot.utils.cli_home import claude_cli_env, cli_timeout_seconds, codex_cli_env, log_cli_timing
from bot.utils.shared_events import _israel_now, clean_form_fields, member_cfg

logger = logging.getLogger(__name__)

# What the AI calls each field -> what the form calls it.
AI_FIELDS = {"title": "title", "event_date": "event_date", "start_time": "event_time",
             "location": "location", "description": "description", "link": "link", "organizer": "organizer"}
_MIMES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
# The bot's own secrets never reach a process that looks at a stranger's picture.
_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
             "SSL_CERT_FILE", "CLAUDE_CODE_OAUTH_TOKEN")
_CODEX_OFF = ("shell_tool", "unified_exec", "view_image", "apps", "plugins", "hooks", "browser_use", "computer_use",
              "image_generation", "multi_agent", "tool_suggest", "skill_search", "code_mode", "sleep_tool")
_running: dict[int, int] = {}


class PictureReadError(RuntimeError):
    """Safe to log: never carries CLI output."""


def _prompt() -> str:
    now = _israel_now()
    return str((member_cfg().get("picture") or {}).get("prompt") or "").replace(
        "{now}", now.strftime("%A %Y-%m-%d %H:%M"))


def _schema() -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(AI_FIELDS),
            "properties": {k: {"type": "string"} for k in AI_FIELDS}}


def _base_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_KEYS if os.environ.get(k)}


def parse_reply(raw: str) -> dict:
    """The AI's answer as a dict. Only a whole-answer code fence is tolerated around it."""
    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except (ValueError, RecursionError):
        raise PictureReadError("answer is not JSON") from None
    if not isinstance(data, dict):
        raise PictureReadError("answer is not a JSON object")
    return data


def _plain(value: str, *, lines: bool) -> str:
    """Visible text only: no control, direction or other invisible format characters."""
    kept = "".join(ch for ch in value if ch == "\n" or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co")
                   or ch in "\t\r‍")
    if not lines:
        return " ".join(kept.split())
    return re.sub(r"\n{3,}", "\n\n", kept.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _iso_date(value: str) -> str:
    """YYYY-MM-DD, also from the way English pages write a full date; '' when unsure."""
    try:  # "20270101" is a valid date but not one the date input can show
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    bare = re.sub(r"^[A-Za-z]+,\s*|,", " ", value)  # weekday and commas
    for fmt in ("%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(" ".join(bare.split()), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _24h(value: str) -> str:
    """'8 PM' and '8:30 p.m.' as HH:MM; anything else is left for the form check."""
    m = re.fullmatch(r"(0?[1-9]|1[0-2])(?::([0-5]\d))?\s*([ap])\.?m\.?", value, re.I)
    if not m:
        return value
    return f"{int(m[1]) % 12 + (12 if m[3].lower() == 'p' else 0):02d}:{m[2] or '00'}"


def clean_reply(data: dict) -> dict:
    """Draft form fields out of an untrusted answer. A field that fails its check is dropped."""
    given = {}
    for key, field in AI_FIELDS.items():
        if isinstance(data.get(key), str):
            given[field] = _plain(data[key], lines=field == "description")
    given["event_date"] = _iso_date(given.get("event_date", ""))
    given["event_time"] = _24h(given.get("event_time", ""))
    fields, errors = clean_form_fields(given)
    return {f: fields[f] for f in AI_FIELDS.values() if fields[f] and f not in errors}


def load_prefill(req: dict | None) -> dict:
    """The stored draft of an open request, checked again (a date may have passed since)."""
    try:
        data = json.loads((req or {}).get("prefill") or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return clean_reply({k: data.get(f) for k, f in AI_FIELDS.items()})


async def _run(cli: str, cmd: list[str], *, stdin: bytes, env: dict, cwd: str, default_s: int) -> bytes:
    started = time.monotonic()
    budget = cli_timeout_seconds(cli, default_s)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=cwd)
    except OSError as exc:
        raise PictureReadError(f"{cli} CLI did not start ({type(exc).__name__})") from None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=budget)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log_cli_timing(cli, "TIMEOUT", time.monotonic() - started, "event-picture")
        raise PictureReadError(f"{cli} CLI timed out after {budget}s") from None
    except BaseException:  # cancelled (shutdown): leave no orphan reading the picture
        if proc.returncode is None:
            proc.kill()
        raise
    log_cli_timing(cli, "ok" if proc.returncode == 0 else "error", time.monotonic() - started, "event-picture")
    if proc.returncode != 0:
        raise PictureReadError(f"{cli} CLI rc={proc.returncode} stdout_chars={len(stdout)} stderr_chars={len(stderr)}")
    return stdout


async def _read_with_codex(picture: bytes, ext: str, codex_bin: str) -> str:
    with tempfile.TemporaryDirectory(prefix="botson-picture-") as tmp:
        image, schema, out = Path(tmp) / f"picture.{ext}", Path(tmp) / "schema.json", Path(tmp) / "answer.txt"
        image.write_bytes(picture)
        schema.write_text(json.dumps(_schema()), encoding="utf-8")
        cmd = [codex_bin, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ignore-user-config",
               "--ignore-rules", "--ephemeral",  # --ephemeral: no session file that would keep the picture
               "--cd", tmp, "-c", 'web_search="disabled"']
        for feature in _CODEX_OFF:
            cmd += ["-c", f"features.{feature}=false"]
        model = os.getenv("BOTSON_CODEX_MODEL", "").strip()
        if model:
            cmd += ["--model", model]
        # --image takes several values, so another option has to follow it before "-".
        cmd += ["--image", str(image), "--output-schema", str(schema), "--output-last-message", str(out), "-"]
        await _run("codex", cmd, stdin=_prompt().encode("utf-8"), env=codex_cli_env(_base_env()), cwd=tmp, default_s=120)
        try:
            return out.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise PictureReadError("codex CLI left no answer") from None


async def _read_with_claude(picture: bytes, ext: str) -> str:
    # The picture goes in through stdin, so Claude needs no file tool (and gets none).
    message = {"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": _MIMES[ext],
                                     "data": base64.b64encode(picture).decode()}},
        {"type": "text", "text": _prompt()}]}}
    model = str((member_cfg().get("picture") or {}).get("claude_model") or "sonnet")
    cmd = ["claude", "-p", "--model", model, "--safe-mode", "--no-session-persistence", "--strict-mcp-config",
           "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--tools", ""]
    with tempfile.TemporaryDirectory(prefix="botson-picture-") as tmp:
        stdout = await _run("claude", cmd, stdin=(json.dumps(message) + "\n").encode("utf-8"),
                            env=claude_cli_env(_base_env()), cwd=tmp, default_s=90)
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            if event.get("is_error") or not isinstance(event.get("result"), str):
                raise PictureReadError("claude CLI reported an error")
            return event["result"]
    raise PictureReadError("claude CLI gave no result")


async def read_event_picture(picture: bytes, ext: str, *, codex_bin: str) -> dict:
    """Draft form fields, or {} when neither CLI could read anything usable."""
    cap = max(1, int((member_cfg().get("picture") or {}).get("max_parallel", 2)))
    loop_id = id(asyncio.get_running_loop())
    while _running.get(loop_id, 0) >= cap:
        await asyncio.sleep(0.5)
    _running[loop_id] = _running.get(loop_id, 0) + 1
    try:
        for name, read in (("codex", lambda: _read_with_codex(picture, ext, codex_bin)),
                           ("claude", lambda: _read_with_claude(picture, ext))):
            started = time.monotonic()
            try:
                fields = clean_reply(parse_reply(await read()))
            except PictureReadError as exc:
                logger.warning("[event_picture] %s failed: %s", name, exc)
                continue
            except Exception as exc:
                # Unknown errors may quote the answer: type only.
                logger.warning("[event_picture] %s failed: %s", name, type(exc).__name__)
                continue
            logger.info("[event_picture] %s read the picture in %.1fs: %s", name, time.monotonic() - started,
                        {k: len(v) for k, v in fields.items()})
            return fields
        logger.warning("[event_picture] neither codex nor claude could read the picture")
        return {}
    finally:
        _running[loop_id] -= 1
        if not _running[loop_id]:
            _running.pop(loop_id, None)
