#!/bin/bash
# verify-sync.sh — 2-second drift check between GitHub and the VPS.
#
# Exits 0 if VPS is clean and up to date with origin/main.
# Exits 1 on any drift.
#
# Run from a local machine at session start, or wire into a SessionStart hook:
#   scripts/verify-sync.sh

set -euo pipefail

VPS_HOST="${ROBOTNIK_VPS:-root@84.46.253.137}"
VPS_DIR="${ROBOTNIK_VPS_DIR:-/opt/robotnik}"
SSH_KEY="${ROBOTNIK_SSH_KEY:-$HOME/.ssh/id_ed25519}"

OUTPUT=$(ssh -i "$SSH_KEY" "$VPS_HOST" "sudo -u botson -H bash -lc '
set -e
cd $VPS_DIR
git fetch --quiet origin main
HEAD=\$(git rev-parse HEAD)
REMOTE=\$(git rev-parse origin/main)
BEHIND=\$(git rev-list HEAD..origin/main --count)
AHEAD=\$(git rev-list origin/main..HEAD --count)
DIRTY=\$(git status --porcelain | grep -v \"^?? media/covers/\" | wc -l)
echo HEAD=\$HEAD
echo REMOTE=\$REMOTE
echo BEHIND=\$BEHIND
echo AHEAD=\$AHEAD
echo DIRTY=\$DIRTY
git status --porcelain | grep -v \"^?? media/covers/\" || true
'")

echo "$OUTPUT"

BEHIND=$(echo "$OUTPUT" | grep '^BEHIND=' | cut -d= -f2)
AHEAD=$(echo "$OUTPUT" | grep '^AHEAD=' | cut -d= -f2)
DIRTY=$(echo "$OUTPUT" | grep '^DIRTY=' | cut -d= -f2)

if [ "$BEHIND" = "0" ] && [ "$AHEAD" = "0" ] && [ "$DIRTY" = "0" ]; then
  echo
  echo "[OK] VPS is clean and in sync with origin/main."
  exit 0
fi

echo
[ "$BEHIND" != "0" ] && echo "[DRIFT] VPS is $BEHIND commit(s) behind origin/main. Run scripts/deploy.sh."
[ "$AHEAD"  != "0" ] && echo "[DRIFT] VPS is $AHEAD commit(s) ahead of origin/main (local commits on VPS — shouldn't happen)."
[ "$DIRTY"  != "0" ] && echo "[DRIFT] VPS has $DIRTY uncommitted edit(s) — someone edited files directly on the VPS."
exit 1
