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
REQUIREMENTS_BEFORE=$(sha256sum requirements.txt | cut -d' ' -f1)

sudo -u "$SERVICE_USER" -H git fetch --quiet origin main
AFTER=$(sudo -u "$SERVICE_USER" -H git rev-parse origin/main)
echo "Target HEAD:   $AFTER"
if [ -n "${DEPLOY_EXPECTED_SHA:-}" ] && [ "$AFTER" != "$DEPLOY_EXPECTED_SHA" ]; then
  echo "Target changed since review; refusing deployment."
  exit 1
fi

# The versioned weekly configuration is runtime state shared by the dashboard,
# agent API and self-service. Keep it in the same canonical settings file across
# code resets. Its lock also prevents losing an in-flight opt-out during deploy.
touch config/.weekly-checkin.lock
chown "$SERVICE_USER:$SERVICE_USER" config/.weekly-checkin.lock
exec 9<>config/.weekly-checkin.lock
flock 9
WEEKLY_SNAPSHOT=$(mktemp "$REPO_DIR/data/weekly-checkin-deploy.XXXXXX")
chmod 600 "$WEEKLY_SNAPSHOT"
.venv/bin/python - "$WEEKLY_SNAPSHOT" <<'PY'
import json,sys,yaml
with open('config/settings.yaml') as source:
    weekly=(yaml.safe_load(source) or {}).get('weekly_state_review') or {}
with open(sys.argv[1], 'w') as output:
    json.dump(weekly if weekly.get('revision', 0) > 0 else None, output)
PY

if [ "$BEFORE" = "$AFTER" ]; then
  echo "Already up to date."
else
  echo
  echo "Commits to apply:"
  sudo -u "$SERVICE_USER" -H git log --oneline "${BEFORE}..${AFTER}" | sed 's/^/  /'
  echo
  echo "=== Applying (git reset --hard origin/main) ==="
  sudo -u "$SERVICE_USER" -H git reset --hard origin/main --quiet

  REQUIREMENTS_AFTER=$(sha256sum requirements.txt | cut -d' ' -f1)
  if [ "$REQUIREMENTS_BEFORE" != "$REQUIREMENTS_AFTER" ]; then
    echo "=== pip install -r requirements.txt ==="
    sudo -u "$SERVICE_USER" -H .venv/bin/pip install --quiet -r requirements.txt
  fi
fi

.venv/bin/python - "$WEEKLY_SNAPSHOT" <<'PY'
import json,os,sys,tempfile,yaml
from pathlib import Path
weekly=json.loads(Path(sys.argv[1]).read_text())
if weekly is not None:
    path=Path('config/settings.yaml')
    settings=yaml.safe_load(path.read_text()) or {}
    settings['weekly_state_review']=weekly
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        os.chmod(output.name, path.stat().st_mode & 0o777)
        yaml.safe_dump(settings, output, allow_unicode=True, sort_keys=False)
        output.flush()
        os.fsync(output.fileno())
        temporary=output.name
    os.replace(temporary,path)
PY
chown "$SERVICE_USER:$SERVICE_USER" config/settings.yaml
flock -u 9
rm -f "$WEEKLY_SNAPSHOT"

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

  # Dual-dispatch guardian: every recurring content type must have exactly ONE
  # active dispatcher (cron OR calendar, never both). Wiring a type into both
  # posted the weekly leaderboard twice (2026-05-23). See dispatch_owner.py.
  if [ -x .venv/bin/pytest ] && [ -f tests/test_no_dual_dispatch.py ]; then
    echo
    echo "=== Dual-dispatch guardian ==="
    if ! sudo -u "$SERVICE_USER" -H .venv/bin/pytest tests/test_no_dual_dispatch.py -q --tb=line; then
      echo
      echo "❌ Dual-dispatch guardian failed — blocking deploy."
      echo "   A content type is reachable by both the cron jobs and the calendar"
      echo "   dispatcher (duplicate-send risk). Fix ownership in"
      echo "   bot/scheduler/dispatch_owner.py and the wiring it points to."
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
