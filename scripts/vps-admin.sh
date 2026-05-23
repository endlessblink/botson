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
#   schedule [days]       Read-only dump of recent game / warm-up scheduled_messages
#                         rows (trivia_round, trivia_warmup_rsvp, warmup_reminder,
#                         emoji_puzzle) with their warmup_marker, min_ready_players,
#                         status, and RSVP counts. days = lookback window (default 3).
#                         Use this to diagnose "game never fired" — e.g. duplicate
#                         warm-up rows sharing one marker, or a row left 'skipped'.
#   applog [lines] [pat]  Read-only tail of data/bot.log (the app log; NOT journald).
#                         lines default 200. Optional pat = grep -E filter, e.g.
#                         'warmup_rsvp_gate|trivia_round'. Use to read dispatch
#                         decisions and skip/fail reasons.
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
  echo "--- scheduled_messages: games + warm-ups, last ${days}d (marker + RSVP counts) ---"
  echo "    rsvps counts trivia_interest_responses for THIS row; duplicate warm-up"
  echo "    rows sharing one marker split the count -> the dispatch gate undercounts."
  sqlite3 -readonly "$DB_PATH" -header -column \
    "SELECT s.id, s.status, s.message_type AS type,
            s.scheduled_date AS d, s.scheduled_time AS t,
            json_extract(s.poll_options,'\$.warmup_marker') AS marker,
            json_extract(s.poll_options,'\$.min_ready_players') AS min_ready,
            (SELECT COUNT(*) FROM trivia_interest_responses r
               WHERE r.scheduled_msg_id = s.id) AS rsvps
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
    applog)        cmd_applog "$@" ;;
    verify-topic)  cmd_verify_topic "$@" ;;
    -h|--help|help|"") usage 0 ;;
    *)
      echo "unknown subcommand: $sub" >&2
      usage 1
      ;;
  esac
}

main "$@"
