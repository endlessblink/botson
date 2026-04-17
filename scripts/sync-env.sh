#!/bin/bash
# Pulls secrets from Doppler (botson/prd) and writes /opt/robotnik/.env
set -e
export DOPPLER_TOKEN=$(cat /opt/robotnik/.doppler/token)
TMP=$(mktemp)
trap 'rm -f $TMP' EXIT
doppler secrets download --project botson --config prd --no-file --format env \
  | grep -v '^DOPPLER_' \
  > "$TMP"
if [ ! -s "$TMP" ]; then
  echo 'sync-env.sh: no secrets downloaded, aborting' >&2
  exit 1
fi
install -m 600 -o botson -g botson "$TMP" /opt/robotnik/.env
echo "sync-env.sh: wrote $(wc -l < /opt/robotnik/.env) secrets to .env"
