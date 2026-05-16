#!/bin/bash
# deploy.sh — pull latest main from GitHub and restart Botson services.
#
# Runs on the VPS at /opt/robotnik. Typical invocation from a local machine:
#   ssh root@vps '/opt/robotnik/scripts/deploy.sh'
#
# Migrations: bot/database/db.py::Database._migrate() runs on bot startup
# (CREATE TABLE IF NOT EXISTS + idempotent ALTER TABLE). No separate step.
#
# Secrets: botson-*.service runs sync-env.sh via ExecStartPre, which
# regenerates /opt/robotnik/.env from Doppler on every restart.

set -euo pipefail

REPO_DIR="/opt/robotnik"
SERVICE_USER="botson"
SERVICES=("botson.service" "botson-dashboard.service")

cd "$REPO_DIR"

echo "=== deploy.sh @ $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Host: $(hostname)  |  Dir: $REPO_DIR  |  As: ${SERVICE_USER}"
echo

BEFORE=$(sudo -u "$SERVICE_USER" -H git rev-parse HEAD)
echo "Current HEAD:  $BEFORE"

sudo -u "$SERVICE_USER" -H git fetch --quiet origin main
AFTER=$(sudo -u "$SERVICE_USER" -H git rev-parse origin/main)
echo "Target HEAD:   $AFTER"

if [ "$BEFORE" = "$AFTER" ]; then
  echo "Already up to date."
else
  echo
  echo "Commits to apply:"
  sudo -u "$SERVICE_USER" -H git log --oneline "${BEFORE}..${AFTER}" | sed 's/^/  /'
  echo
  echo "=== Applying (git reset --hard origin/main) ==="
  sudo -u "$SERVICE_USER" -H git reset --hard origin/main --quiet

  if [ -f requirements.txt ]; then
    echo "=== pip install -r requirements.txt ==="
    sudo -u "$SERVICE_USER" -H .venv/bin/pip install --quiet -r requirements.txt
  fi
fi

# Hardcoded-content guardian gate. See CLAUDE.md → "No Hardcoded
# User-Facing Content". Bypass with SKIP_HARDCODED_GUARDIAN=1 for
# emergencies; the bypass is logged audibly so it shows up in deploy
# output for after-the-fact review.
if [ "${SKIP_HARDCODED_GUARDIAN:-0}" != "1" ]; then
  if [ -x .venv/bin/pytest ] && [ -f tests/test_no_hardcoded_content.py ]; then
    echo
    echo "=== Hardcoded-content guardian ==="
    if ! sudo -u "$SERVICE_USER" -H .venv/bin/pytest tests/test_no_hardcoded_content.py -q --tb=line; then
      echo
      echo "❌ Guardian failed — blocking deploy. Fix the violations above"
      echo "   or rerun with SKIP_HARDCODED_GUARDIAN=1 if this is an emergency."
      exit 1
    fi
  fi
  # T-171 discussion pool validator: same blocking semantics. Bypass via
  # the same SKIP_HARDCODED_GUARDIAN flag (the two guardians share a
  # category of "content-shape rules enforced at deploy").
  if [ -x .venv/bin/pytest ] && [ -f tests/test_discussion_pool_quality.py ]; then
    echo
    echo "=== Discussion pool validator ==="
    if ! sudo -u "$SERVICE_USER" -H .venv/bin/pytest tests/test_discussion_pool_quality.py -q --tb=line; then
      echo
      echo "❌ Pool validator failed — blocking deploy. Fix the entries above,"
      echo "   or allowlist them in config/discussion_pool_baseline.yaml."
      exit 1
    fi
  fi

  # T-189 abstraction-over-enumeration guardian: any auto-learned rule
  # that quotes a draft >40 chars verbatim is memorization, not learning.
  # See CLAUDE.md ⚠ "Abstraction over enumeration".
  if [ -x .venv/bin/pytest ] && [ -f tests/test_no_verbatim_quotes_in_rules.py ]; then
    echo
    echo "=== No-verbatim-quotes guardian ==="
    if ! sudo -u "$SERVICE_USER" -H .venv/bin/pytest tests/test_no_verbatim_quotes_in_rules.py -q --tb=line; then
      echo
      echo "❌ Verbatim-quote guardian failed — blocking deploy."
      echo "   A learned rule contains too much of a rejected draft (>40 chars)."
      echo "   This is memorization, not learning."
      echo "   Fix: ensure _llm_abstract_rules wrote the rules, not a concat fallback."
      exit 1
    fi
  fi
else
  echo
  echo "⚠️  SKIP_HARDCODED_GUARDIAN=1 — bypassing guardians (logged for audit)."
fi

echo
echo "=== Restarting services ==="
for svc in "${SERVICES[@]}"; do
  echo "  restart $svc"
  systemctl restart "$svc"
done

sleep 4

echo
echo "=== Post-restart status ==="
FAILED=0
for svc in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "$svc"; then
    echo "  [ok]   $svc"
  else
    echo "  [FAIL] $svc"
    FAILED=1
  fi
done

if [ $FAILED -eq 1 ]; then
  echo
  echo "One or more services failed. Recent logs:"
  for svc in "${SERVICES[@]}"; do
    echo "--- $svc ---"
    journalctl -u "$svc" -n 10 --no-pager | tail -10
  done
  exit 1
fi

echo
echo "=== Deploy complete. HEAD=$AFTER ==="
