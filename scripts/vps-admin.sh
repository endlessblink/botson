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
#
# Routine ops live here so the agent can be allowlisted to call this script
# without granting blanket SSH-write privileges. Dangerous logic is reviewed
# in code, not in opaque allowlist regexes.

set -euo pipefail

BOT_SVC="botson.service"
DASH_SVC="botson-dashboard.service"

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
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

main() {
  local sub="${1:-}"
  shift || true
  case "$sub" in
    tz)      cmd_tz "$@" ;;
    restart) cmd_restart "$@" ;;
    status)  cmd_status "$@" ;;
    logs)    cmd_logs "$@" ;;
    -h|--help|help|"") usage 0 ;;
    *)
      echo "unknown subcommand: $sub" >&2
      usage 1
      ;;
  esac
}

main "$@"
