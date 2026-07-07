#!/bin/bash
# Install/update Botson health guard systemd timers on the VPS.

set -euo pipefail

REPO_DIR="${BOTSON_REPO_DIR:-/opt/robotnik}"
UNIT_DIR="${BOTSON_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
UNITS=(
  botson-health-daily.service
  botson-health-daily.timer
  botson-health-weekly.service
  botson-health-weekly.timer
)

if [ "$(id -u)" != "0" ]; then
  echo "install_health_guard_timers.sh must run as root" >&2
  exit 1
fi

for unit in "${UNITS[@]}"; do
  install -m 0644 "$REPO_DIR/systemd/$unit" "$UNIT_DIR/$unit"
done

systemctl daemon-reload
systemctl enable --now botson-health-daily.timer botson-health-weekly.timer

echo "Installed Botson health guard timers:"
systemctl list-timers --all --no-pager botson-health-daily.timer botson-health-weekly.timer
