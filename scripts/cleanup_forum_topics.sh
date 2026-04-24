#!/bin/bash
# One-shot cleanup: remove unverified placeholder rows from forum_topics.
#
# Context: earlier versions of bot/handlers/topic_tracker.py stored a literal
# "Topic {thread_id}" string whenever the bot saw a message in an unknown
# thread without a forum_topic_created service message attached. Those rows
# never got upgraded and polluted the scheduler's channel picker.
#
# The tracker was patched to stop creating placeholders, but existing rows
# are still sitting in the DB. This script backs up, deletes anything not
# in verified_forum_topics, and prints the remaining rows.
#
# Safe to re-run: the DELETE is idempotent once forum_topics == verified set.

set -e

DB="${1:-/opt/robotnik/data/bot.db}"
TS=$(date +%Y%m%d-%H%M%S)
BK="${DB}.bak.pre-topic-cleanup.${TS}"

echo "=== cleanup_forum_topics.sh @ $(date) ==="
echo "DB:     $DB"
echo "Backup: $BK"
echo ""

echo "=== 1. Backup ==="
cp -a "$DB" "$BK"
ls -la "$BK"
echo ""

echo "=== 2. Pre-cleanup counts ==="
sudo -u botson sqlite3 "$DB" <<'SQL'
SELECT 'verified:     ' || COUNT(*) FROM verified_forum_topics;
SELECT 'forum_topics: ' || COUNT(*) FROM forum_topics;
SELECT 'to_remove:    ' || COUNT(*) FROM forum_topics
 WHERE topic_id NOT IN (SELECT topic_id FROM verified_forum_topics);
SELECT '-- rows that will be removed --';
SELECT topic_id, name FROM forum_topics
 WHERE topic_id NOT IN (SELECT topic_id FROM verified_forum_topics)
 ORDER BY topic_id;
SQL
echo ""

echo "=== 3. Prune ==="
sudo -u botson sqlite3 "$DB" \
  "DELETE FROM forum_topics WHERE topic_id NOT IN (SELECT topic_id FROM verified_forum_topics);"
echo "done"
echo ""

echo "=== 4. Post-cleanup state ==="
sudo -u botson sqlite3 "$DB" <<'SQL'
SELECT 'forum_topics: ' || COUNT(*) FROM forum_topics;
SELECT '-- remaining rows --';
SELECT topic_id, name FROM forum_topics ORDER BY topic_id;
SQL
echo ""

echo "Backup preserved at: $BK"
echo "To revert: sudo -u botson cp -a $BK $DB && systemctl restart botson botson-dashboard"
