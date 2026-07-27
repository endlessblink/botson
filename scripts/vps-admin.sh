#!/bin/bash
# vps-admin.sh — scoped VPS administrative ops for Botson.
#
# Runs on the VPS at /opt/robotnik. Typical invocation from a local machine:
#   ssh -i ~/.ssh/id_ed25519 root@84.46.253.137 \
#     '/opt/robotnik/scripts/vps-admin.sh <subcommand> [args]'
#
# Subcommands:
#   tz <zone>             Set system timezone (e.g. Asia/Jerusalem) and print before/after.
#   restart [target]      Restart services. target = bot | dash | both (default: both).
#   status                Show timezone, current date, and service active state.
#   logs <target> [n]     Tail journalctl. target = bot | dash. n = lines (default: 100).
#   topics                Read-only dump of verified_forum_topics from data/bot.db.
#                         Source of truth for "which Telegram topic_id maps to which
#                         category" — see CLAUDE.md "Forum Topic IDs". Use this
#                         after sending a dot from the dashboard to find the new id.
#   routing               Read-only dump of bot_message_routing — which handler posts
#                         to which topic. Editable from the dashboard, not via this
#                         script.
#   schedule [days]       Read-only dump of recent/future game / warm-up scheduled_messages
#                         rows (trivia_round, trivia_warmup_rsvp, warmup_reminder,
#                         emoji_puzzle) with their warmup_marker, min_ready_players,
#                         topic, Telegram message id, status, and RSVP counts.
#                         days = lookback window; future rows are included (default 3).
#                         Use this to diagnose "game never fired" — e.g. duplicate
#                         warm-up rows sharing one marker, or a row left 'skipped'.
#   applog [lines] [pat]  Read-only tail of data/bot.log (the app log; NOT journald).
#                         lines default 200. Optional pat = grep -E filter, e.g.
#                         'warmup_rsvp_gate|trivia_round'. Use to read dispatch
#                         decisions and skip/fail reasons.
#   llm-doctor            Read-only diagnosis of the generation CLIs (Claude + Codex):
#                         which HOME the service actually runs with, where each
#                         CLI's credentials live, whether they are readable and
#                         unexpired, CLI versions, and how often calls have been
#                         timing out. Prints NO secrets — only paths, expiry
#                         timestamps, and counts. Run this FIRST whenever the
#                         planner reports "Aborted" or generation is slow; a
#                         90s-timeout-per-row pattern is almost always a
#                         credentials-location problem, not a model problem.
#   health                Read-only dump of Botson health guard timer state,
#                         last persisted result, and recent health service logs.
#   activity [n] [filter] Read-only dump of the activity_log table (most recent n,
#                         default 50). Optional filter matches action_type OR
#                         description (LIKE). This is where game outcomes that are
#                         NOT in bot.log land — e.g. "סיבוב טריוויה בוטל — 0/2 מוכנים".
#                         Try: activity 100 trivia_round
#   verify-topic <id> <category_key> <verified_name>
#                         Register a forum topic as operator-verified so safe_send
#                         will post into it. Mirrors the dashboard "send dot" +
#                         confirm flow without needing a session cookie. Calls
#                         Database.upsert_forum_topic + upsert_verified_forum_topic
#                         (the same path the dashboard uses).
#
# Routine ops live here so the agent can be allowlisted to call this script
# without granting blanket SSH-write privileges. Dangerous logic is reviewed
# in code, not in opaque allowlist regexes.

set -euo pipefail

BOT_SVC="botson.service"
DASH_SVC="botson-dashboard.service"

usage() {
  # Print every leading comment line until the first blank-or-non-comment.
  awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
  exit "${1:-1}"
}

resolve_target() {
  case "${1:-both}" in
    bot)   echo "$BOT_SVC" ;;
    dash)  echo "$DASH_SVC" ;;
    both)  echo "$BOT_SVC $DASH_SVC" ;;
    *)     echo "ERR" ;;
  esac
}

cmd_tz() {
  local zone="${1:-}"
  if [ -z "$zone" ]; then
    echo "usage: vps-admin.sh tz <zone>" >&2
    echo "       e.g. vps-admin.sh tz Asia/Jerusalem" >&2
    exit 2
  fi

  echo "=== vps-admin.sh tz @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  echo "--- Before ---"
  timedatectl status | head -5
  echo

  timedatectl set-timezone "$zone"

  echo "--- After ---"
  timedatectl status | head -5
  echo
  echo "Wall clock: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo
  echo "NOTE: Long-running services (botson, botson-dashboard) cache TZ in their"
  echo "      env at startup. Run 'vps-admin.sh restart both' to apply."
}

cmd_restart() {
  local target_arg="${1:-both}"
  local services
  services=$(resolve_target "$target_arg")
  if [ "$services" = "ERR" ]; then
    echo "usage: vps-admin.sh restart [bot|dash|both]" >&2
    exit 2
  fi

  echo "=== vps-admin.sh restart @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo "Target: $target_arg ($services)"
  echo

  for svc in $services; do
    echo "  restart $svc"
    systemctl restart "$svc"
  done

  sleep 4

  echo
  echo "--- Post-restart status ---"
  local failed=0
  for svc in $services; do
    if systemctl is-active --quiet "$svc"; then
      echo "  [ok]   $svc"
    else
      echo "  [FAIL] $svc"
      failed=1
    fi
  done

  if [ $failed -eq 1 ]; then
    echo
    echo "One or more services failed. Recent logs:"
    for svc in $services; do
      echo "--- $svc ---"
      journalctl -u "$svc" -n 10 --no-pager | tail -10
    done
    exit 1
  fi
}

cmd_status() {
  echo "=== vps-admin.sh status @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  echo "--- Time ---"
  timedatectl status | head -5
  echo
  echo "--- Services ---"
  for svc in "$BOT_SVC" "$DASH_SVC"; do
    if systemctl is-active --quiet "$svc"; then
      echo "  [ok]   $svc"
    else
      echo "  [FAIL] $svc"
    fi
  done
}

cmd_logs() {
  local target_arg="${1:-}"
  local lines="${2:-100}"
  local svc
  case "$target_arg" in
    bot)  svc="$BOT_SVC" ;;
    dash) svc="$DASH_SVC" ;;
    *)
      echo "usage: vps-admin.sh logs <bot|dash> [lines]" >&2
      exit 2
      ;;
  esac
  journalctl -u "$svc" -n "$lines" --no-pager
}

# Read-only DB diagnostics. sqlite3 -readonly hard-blocks any write even if
# the query is malformed. The agent uses these to answer "what's the topic_id
# for X?" and "which handler is routed where?" without needing a session
# cookie on the dashboard or pasting from a screenshot.
DB_PATH="/opt/robotnik/data/bot.db"

_require_sqlite3() {
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "sqlite3 not installed on VPS — install with: apt-get install -y sqlite3" >&2
    exit 1
  fi
  if [ ! -f "$DB_PATH" ]; then
    echo "DB not found: $DB_PATH" >&2
    exit 1
  fi
}

cmd_topics() {
  echo "=== vps-admin.sh topics @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  _require_sqlite3
  echo "--- verified_forum_topics (the dot-test source of truth) ---"
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT topic_id, category_key, verified_name, verification_source, verified_at
       FROM verified_forum_topics
       ORDER BY topic_id"
}

cmd_routing() {
  echo "=== vps-admin.sh routing @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  _require_sqlite3
  echo "--- bot_message_routing (per-handler topic targets) ---"
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT handler, play_topic_id, teaser_topic_ids, updated_at
       FROM bot_message_routing
       ORDER BY handler"
}

cmd_schedule() {
  local days="${1:-3}"
  case "$days" in
    ''|*[!0-9]*) echo "usage: vps-admin.sh schedule [days]  (days = integer, default 3)" >&2; exit 2 ;;
  esac
  echo "=== vps-admin.sh schedule @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  _require_sqlite3
  echo "--- scheduled_messages: games + warm-ups, since last ${days}d (future included) ---"
  echo "    row_rsvps counts responses for THIS row. marker_rsvps aggregates every"
  echo "    sent warm-up row sharing the same marker, matching the dispatch gate."
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT s.id, s.status, s.message_type AS type,
            s.scheduled_date AS d, s.scheduled_time AS t,
            s.channel_topic_id AS topic,
            s.sent_message_id AS tg_msg,
            json_extract(s.poll_options,'\$.theme_label') AS theme,
            json_extract(s.poll_options,'\$.media_types') AS media,
            json_extract(s.poll_options,'\$.warmup_marker') AS marker,
            json_extract(s.poll_options,'\$.min_ready_players') AS min_ready,
            (SELECT COUNT(*) FROM trivia_interest_responses r
               WHERE r.scheduled_msg_id = s.id) AS row_rsvps,
            (SELECT COUNT(DISTINCT r.user_id)
               FROM trivia_interest_responses r
               JOIN scheduled_messages w ON w.id = r.scheduled_msg_id
              WHERE w.message_type = 'trivia_warmup_rsvp'
                AND w.status = 'sent'
                AND json_extract(w.poll_options,'\$.warmup_marker') =
                    json_extract(s.poll_options,'\$.warmup_marker')) AS marker_rsvps
       FROM scheduled_messages s
      WHERE s.message_type IN
            ('trivia_round','trivia_warmup_rsvp','warmup_reminder','emoji_puzzle')
        AND s.scheduled_date >= date('now','-${days} days')
      ORDER BY s.id"
}

cmd_applog() {
  local lines="${1:-200}"
  local pattern="${2:-}"
  case "$lines" in
    ''|*[!0-9]*) echo "usage: vps-admin.sh applog [lines] [grep-pattern]" >&2; exit 2 ;;
  esac
  local log="/opt/robotnik/data/bot.log"
  if [ ! -f "$log" ]; then
    echo "app log not found: $log" >&2
    exit 1
  fi
  echo "=== vps-admin.sh applog @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  # Logs rotate ~daily (10MB RotatingFileHandler). With a pattern, search the
  # rotated set too (oldest .3 -> current) so yesterday's events are reachable;
  # without one, just tail the live file.
  if [ -n "$pattern" ]; then
    local files=()
    local f
    for f in "$log.3" "$log.2" "$log.1" "$log"; do
      [ -f "$f" ] && files+=("$f")
    done
    echo "files=${files[*]} lines=$lines pattern=$pattern"
    echo
    grep -ahE "$pattern" "${files[@]}" | tail -n "$lines"
  else
    echo "file=$log lines=$lines pattern=(none)"
    echo
    tail -n "$lines" "$log"
  fi
}

cmd_llm_doctor() {
  echo "=== vps-admin.sh llm-doctor @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  echo "--- Service identity ---"
  local svc_home svc_user pid
  svc_user="$(systemctl show -p User --value "$DASH_SVC" 2>/dev/null || echo '?')"
  pid="$(systemctl show -p MainPID --value "$DASH_SVC" 2>/dev/null || echo 0)"
  svc_home=""
  if [ "${pid:-0}" -gt 0 ] && [ -r "/proc/$pid/environ" ]; then
    svc_home="$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^HOME=//p' | head -1)"
  fi
  echo "user=${svc_user:-?}  pid=${pid:-0}  HOME=${svc_home:-<unreadable>}"
  echo "passwd home for ${svc_user}: $(getent passwd "$svc_user" | cut -d: -f6 2>/dev/null || echo '?')"
  echo

  echo "--- Credential locations (no secrets printed) ---"
  local found_claude=0 found_codex=0 d
  for d in "$svc_home" "$(getent passwd "$svc_user" | cut -d: -f6 2>/dev/null)" /opt/robotnik; do
    [ -n "$d" ] || continue
    if [ -f "$d/.claude/.credentials.json" ]; then
      echo "claude creds: $d/.claude/.credentials.json ($(stat -c '%U:%G %a %y' "$d/.claude/.credentials.json" | cut -d. -f1))"
      found_claude=1
    fi
    if [ -f "$d/.codex/auth.json" ]; then
      echo "codex  auth : $d/.codex/auth.json"
      found_codex=1
    fi
  done
  if [ -f /opt/robotnik/.codex-home/auth.json ]; then
    echo "codex  auth : /opt/robotnik/.codex-home/auth.json"
    found_codex=1
  fi
  [ "$found_claude" -eq 1 ] || echo "claude creds: NONE FOUND — every call will hang until timeout"
  [ "$found_codex" -eq 1 ] || echo "codex  auth : NONE FOUND — fallback cannot run"
  echo

  echo "--- Claude token expiry ---"
  local creds
  creds="$(ls -1 /opt/robotnik/.claude/.credentials.json "$svc_home/.claude/.credentials.json" 2>/dev/null | head -1 || true)"
  if [ -n "$creds" ]; then
    python3 - "$creds" <<'PY' || echo "  (could not parse)"
import datetime, json, sys
with open(sys.argv[1]) as fh:
    oauth = (json.load(fh) or {}).get("claudeAiOauth") or {}
exp = oauth.get("expiresAt")
print("  plan:", oauth.get("subscriptionType", "?"))
if exp:
    when = datetime.datetime.fromtimestamp(exp / 1000, datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    mins = (when - now).total_seconds() / 60
    state = "VALID" if mins > 0 else "EXPIRED"
    print(f"  token: {state}, {abs(mins):.0f} min {'left' if mins > 0 else 'ago'} (expires {when:%Y-%m-%d %H:%M} UTC)")
else:
    print("  token: no expiry field")
PY
  else
    echo "  no readable credentials file"
  fi
  echo

  echo "--- Home writability (a non-writable home breaks the codex guard) ---"
  for d in "$svc_home" "$(getent passwd "$svc_user" | cut -d: -f6 2>/dev/null)"; do
    [ -n "$d" ] || continue
    if sudo -u "$svc_user" test -w "$d" 2>/dev/null; then
      echo "  $d: writable by $svc_user"
    else
      echo "  $d: NOT writable by $svc_user  (owner: $(stat -c '%U' "$d" 2>/dev/null || echo '?'))"
    fi
  done
  echo

  echo "--- CLI binaries ---"
  echo "  claude: $(command -v claude || echo 'not on PATH')"
  echo "  codex : $(command -v codex || echo 'not on PATH')"
  echo

  echo "--- Timeout / fallback frequency (last 7 days) ---"
  echo "  claude CLI timeouts : $(journalctl -u "$DASH_SVC" --since '7 days ago' --no-pager 2>/dev/null | grep -c 'CLI timed out after 90s' || true)"
  echo "  codex  CLI timeouts : $(journalctl -u "$DASH_SVC" --since '7 days ago' --no-pager 2>/dev/null | grep -c 'Codex CLI timed out' || true)"
  echo "  codex  fallback used: $(journalctl -u "$DASH_SVC" --since '7 days ago' --no-pager 2>/dev/null | grep -c 'Codex CLI fallback was used' || true)"
  echo "  non-zero CLI exits  : $(journalctl -u "$DASH_SVC" --since '7 days ago' --no-pager 2>/dev/null | grep -c 'CLI error (rc=' || true)"
  echo
  echo "  Reading: timeouts with zero non-zero exits mean the CLI never returns —"
  echo "  either it is hanging or the call genuinely exceeds the 90s budget. Rule"
  echo "  out credentials first (the resolved HOME below must contain the creds"
  echo "  listed above, and the token must be VALID). If credentials are fine, the"
  echo "  next measurement is a timed call on this host — the budget may just be"
  echo "  too small for the prompt size."
  echo
  echo "--- Resolution the app will use ---"
  ( cd /opt/robotnik && sudo -u "$svc_user" HOME="${svc_home:-/opt/robotnik}" \
      PYTHONPATH=/opt/robotnik /opt/robotnik/.venv/bin/python -c '
from bot.utils.cli_home import resolve_claude_home, resolve_codex_home
print("  claude HOME ->", resolve_claude_home())
print("  CODEX_HOME  ->", resolve_codex_home() or "<unset, CLI default>")
' 2>&1 | tail -5 ) || echo "  (resolution probe unavailable)"
}

cmd_health() {
  echo "=== vps-admin.sh health @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  echo "--- Health timers ---"
  systemctl list-timers --all --no-pager botson-health-daily.timer botson-health-weekly.timer || true
  echo
  echo "--- Health services ---"
  for svc in botson-health-daily.service botson-health-weekly.service; do
    if systemctl is-failed --quiet "$svc"; then
      echo "  [FAIL] $svc"
    else
      state=$(systemctl show "$svc" -p ActiveState -p Result --value | tr '\n' '/' | sed 's:/$::')
      echo "  [ok]   $svc ($state)"
    fi
  done
  echo
  echo "--- Last state ---"
  local state_file="/opt/robotnik/data/health_guard_state.json"
  if [ -f "$state_file" ]; then
    python3 -m json.tool "$state_file" 2>/dev/null || cat "$state_file"
  else
    echo "No state file yet: $state_file"
  fi
  echo
  echo "--- Recent daily health log ---"
  journalctl -u botson-health-daily.service -n 60 --no-pager || true
  echo
  echo "--- Recent weekly health log ---"
  journalctl -u botson-health-weekly.service -n 40 --no-pager || true
}

cmd_activity() {
  local lines="${1:-50}"
  local pattern="${2:-}"
  case "$lines" in
    ''|*[!0-9]*) echo "usage: vps-admin.sh activity [lines] [action_type-or-description-filter]" >&2; exit 2 ;;
  esac
  echo "=== vps-admin.sh activity @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  _require_sqlite3
  local where=""
  if [ -n "$pattern" ]; then
    local safe="${pattern//\'/\'\'}"   # escape single quotes for SQL literal
    where="WHERE action_type LIKE '%${safe}%' OR description LIKE '%${safe}%'"
  fi
  echo "--- activity_log (most recent ${lines}${pattern:+, filter='$pattern'}) ---"
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT id, timestamp, action_type, substr(description,1,90) AS description
       FROM activity_log
       ${where}
       ORDER BY id DESC
       LIMIT ${lines}"
}

cmd_rows() {
  # Read-only dump of ALL scheduled_messages (every type, not just games) in a
  # date window, so we can see whether a day is already committed vs genuinely
  # empty. Diagnostic only — no writes.
  local days="${1:-7}"
  case "$days" in
    ''|*[!0-9]*) echo "usage: vps-admin.sh rows [days]  (days = integer forward window, default 7)" >&2; exit 2 ;;
  esac
  echo "=== vps-admin.sh rows @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo
  _require_sqlite3
  echo "--- scheduled_messages: today .. +${days}d, all types ---"
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT scheduled_date AS d, scheduled_time AS t, message_type AS type,
            status, channel_topic_id AS topic, substr(text,1,40) AS text
       FROM scheduled_messages
      WHERE scheduled_date >= date('now')
        AND scheduled_date <= date('now','+${days} days')
      ORDER BY scheduled_date, scheduled_time"
  echo
  echo "--- per-day counts by status ---"
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT scheduled_date AS d, status, COUNT(*) AS n
       FROM scheduled_messages
      WHERE scheduled_date >= date('now')
        AND scheduled_date <= date('now','+${days} days')
      GROUP BY scheduled_date, status
      ORDER BY scheduled_date, status"
}

cmd_verify_topic() {
  local topic_id="${1:-}" category="${2:-}" name="${3:-}"
  if [ -z "$topic_id" ] || [ -z "$category" ] || [ -z "$name" ]; then
    echo "usage: vps-admin.sh verify-topic <topic_id> <category_key> <verified_name>" >&2
    echo "       e.g. verify-topic 4502 music 'חדר מוסיקה'" >&2
    exit 2
  fi
  case "$topic_id" in
    ''|*[!0-9]*) echo "topic_id must be an integer, got: $topic_id" >&2; exit 2 ;;
  esac
  case "$category" in
    ''|*[!a-z_]*) echo "category_key must be lowercase letters/underscores only, got: $category" >&2; exit 2 ;;
  esac
  echo "=== vps-admin.sh verify-topic @ $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  echo "topic_id=$topic_id  category=$category  name=$name"
  echo

  # Run the upsert as the botson user so DB ownership stays consistent
  # (data/bot.db is botson:botson). Pass the Hebrew name via env to avoid
  # shell-quoting issues.
  cd /opt/robotnik
  TOPIC_ID="$topic_id" CATEGORY="$category" VNAME="$name" \
    sudo -u botson env "TOPIC_ID=$topic_id" "CATEGORY=$category" "VNAME=$name" \
    .venv/bin/python -c '
import asyncio, os
from bot.database.db import Database

async def main():
    db = Database("/opt/robotnik/data/bot.db")
    await db.init()
    tid = int(os.environ["TOPIC_ID"])
    cat = os.environ["CATEGORY"]
    name = os.environ["VNAME"]
    await db.upsert_forum_topic(tid, name)
    await db.upsert_verified_forum_topic(
        topic_id=tid,
        verified_name=name,
        category_key=cat,
        verification_source="operator-supplied via vps-admin.sh verify-topic",
    )
    await db.close()
    print(f"verified: topic_id={tid}, category_key={cat}, verified_name={name}")

asyncio.run(main())
'
  echo
  echo "--- post-write state ---"
  _require_sqlite3
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT topic_id, category_key, verified_name FROM verified_forum_topics WHERE category_key = '$category'"
}

main() {
  local sub="${1:-}"
  shift || true
  case "$sub" in
    tz)       cmd_tz "$@" ;;
    restart)  cmd_restart "$@" ;;
    status)   cmd_status "$@" ;;
    logs)     cmd_logs "$@" ;;
    topics)        cmd_topics "$@" ;;
    routing)       cmd_routing "$@" ;;
    schedule)      cmd_schedule "$@" ;;
    rows)          cmd_rows "$@" ;;
    applog)        cmd_applog "$@" ;;
    health)        cmd_health "$@" ;;
    llm-doctor)    cmd_llm_doctor "$@" ;;
    activity)      cmd_activity "$@" ;;
    verify-topic)  cmd_verify_topic "$@" ;;
    -h|--help|help|"") usage 0 ;;
    *)
      echo "unknown subcommand: $sub" >&2
      usage 1
      ;;
  esac
}

main "$@"
