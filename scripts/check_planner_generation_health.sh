#!/bin/bash
# Read-only Planner generation health check.
#
# Designed for cron / Watchpost on the VPS. It runs the provider-chain check
# plus a synthetic Planner dry-run and exits non-zero on degraded/failed health.
#
# Optional env:
#   BOTSON_GENERATION_HEALTH_TIMEOUT_SECONDS=420
#   BOTSON_GENERATION_HEALTH_MIN_SUGGESTIONS=6
#   BOTSON_GENERATION_HEALTH_ALLOW_DEGRADED=1

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${BOTSON_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SERVICE_USER="${BOTSON_SERVICE_USER:-botson}"
TIMEOUT_SECONDS="${BOTSON_GENERATION_HEALTH_TIMEOUT_SECONDS:-420}"
MIN_SUGGESTIONS="${BOTSON_GENERATION_HEALTH_MIN_SUGGESTIONS:-6}"

case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*) echo "BOTSON_GENERATION_HEALTH_TIMEOUT_SECONDS must be an integer" >&2; exit 2 ;;
esac
case "$MIN_SUGGESTIONS" in
  ''|*[!0-9]*) echo "BOTSON_GENERATION_HEALTH_MIN_SUGGESTIONS must be an integer" >&2; exit 2 ;;
esac

cd "$REPO_DIR"

outer_timeout=$((TIMEOUT_SECONDS + 30))
cmd=(
  timeout "$outer_timeout"
  scripts/check_generation_health.py
  --planner
  --min-suggestions "$MIN_SUGGESTIONS"
  --timeout-seconds "$TIMEOUT_SECONDS"
)

if [ "${BOTSON_GENERATION_HEALTH_ALLOW_DEGRADED:-0}" = "1" ]; then
  cmd+=(--allow-degraded)
fi

if [ "$(id -u)" = "0" ] && id "$SERVICE_USER" >/dev/null 2>&1; then
  exec sudo -u "$SERVICE_USER" env HOME="$REPO_DIR" "${cmd[@]}"
fi

exec env HOME="${HOME:-$REPO_DIR}" "${cmd[@]}"
