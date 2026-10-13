#!/usr/bin/env bash
# Normal workstation entry point. No transfer/SSH occurs for --preflight-only.
set -euo pipefail
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
REGISTRY_DIR="${BOT_REGISTRY_DIR:-$(dirname -- "$REPO_DIR")/bots-directory}"
CHECKER="${BOT_WORKFLOW_CHECKER:-$REGISTRY_DIR/tools/check_bot_workflow.py}"
REGISTRY="${BOT_REGISTRY_PATH:-$REGISTRY_DIR/bot-registry.yaml}"
PYTHON="${BOT_WORKFLOW_PYTHON:-python3}"
VPS="root@84.46.253.137"
REMOTE="/opt/robotnik"
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--preflight-only" ) ]]; then
  echo "Usage: scripts/deploy-from-local.sh [--preflight-only]" >&2
  exit 2
fi
if [[ ! -f "$CHECKER" || ! -f "$REGISTRY" || ! -f "$(dirname -- "$CHECKER")/validate_registry.py" ]]; then
  echo "Deployment stopped: registry tools/index missing. Restore bots-directory or configure BOT_WORKFLOW_CHECKER and BOT_REGISTRY_PATH." >&2
  exit 2
fi

# Pin one non-secret snapshot for both sides; the canonical checker still validates its schema.
BUNDLE="$(mktemp -d)"
REMOTE_BUNDLE=""
cleanup() {
  rm -rf -- "$BUNDLE"
  if [[ -n "$REMOTE_BUNDLE" ]]; then
    ssh "$VPS" "rm -rf -- '$REMOTE_BUNDLE'" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
cp -- "$REGISTRY" "$BUNDLE/bot-registry.yaml"
cp -- "$CHECKER" "$BUNDLE/check_bot_workflow.py"
cp -- "$(dirname -- "$CHECKER")/validate_registry.py" "$BUNDLE/validate_registry.py"
"$PYTHON" "$BUNDLE/check_bot_workflow.py" --registry "$BUNDLE/bot-registry.yaml" \
  --bot botson --checkout "$REPO_DIR" --intent deploy \
  --deployment-host "${VPS#*@}" --deployment-path "$REMOTE" || {
    echo "Deployment stopped before SSH: reconcile Botson's registry reference, source checkout and release target." >&2
    exit 2
  }
if [[ "${1:-}" == "--preflight-only" ]]; then exit 0; fi
if ! cmp -s -- "$REGISTRY" "$BUNDLE/bot-registry.yaml"; then
  echo "Deployment stopped: registry changed after preflight. Retry against current content." >&2
  exit 2
fi

REMOTE_BUNDLE="$(ssh "$VPS" 'mktemp -d /tmp/botson-registry.XXXXXXXX')"
# Accept only the directory pattern produced above before quoting it in remote commands.
if [[ ! "$REMOTE_BUNDLE" =~ ^/tmp/botson-registry\.[A-Za-z0-9]{8}$ ]]; then
  REMOTE_BUNDLE=""
  echo "Deployment stopped: unexpected temporary directory from VPS." >&2
  exit 2
fi
scp -- "$BUNDLE/check_bot_workflow.py" "$BUNDLE/validate_registry.py" "$BUNDLE/bot-registry.yaml" "$VPS:$REMOTE_BUNDLE/"
# Validate the actual VPS release directory even while its installed deploy.sh is an older version.
# Future released deploy.sh receives the same tools and repeats its own boundary check.
ssh "$VPS" "set -eu
  /opt/robotnik/.venv/bin/python '$REMOTE_BUNDLE/check_bot_workflow.py' --registry '$REMOTE_BUNDLE/bot-registry.yaml' --bot botson --checkout '$REMOTE' --checkout-host '${VPS#*@}' --intent deploy --deployment-host '${VPS#*@}' --deployment-path '$REMOTE'
  export BOT_WORKFLOW_CHECKER='$REMOTE_BUNDLE/check_bot_workflow.py'
  export BOT_REGISTRY_PATH='$REMOTE_BUNDLE/bot-registry.yaml'
  exec bash '$REMOTE/scripts/deploy.sh'
"
