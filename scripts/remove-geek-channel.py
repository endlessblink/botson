"""One-shot cleanup: remove all references to the deleted geek channel (topic 442).

Cancels all scheduled_messages targeting topic 442, removes the verified
forum-topic mapping, and clears teaser_topic_ids in bot_message_routing
that include 442.

Idempotent: safe to run multiple times.
"""
import json
import os
import sqlite3
import sys

DB_PATH = os.environ.get("DB_PATH", "data/bot.db")
GEEK_TOPIC_ID = 442

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
cur = con.cursor()

# 1. Cancel future scheduled_messages targeting topic 442 (don't touch already-sent rows)
cur.execute(
    "SELECT COUNT(*) FROM scheduled_messages "
    "WHERE channel_topic_id = ? AND status NOT IN ('sent', 'cancelled')",
    (GEEK_TOPIC_ID,),
)
to_cancel = cur.fetchone()[0]
cur.execute(
    "UPDATE scheduled_messages SET status = 'cancelled' "
    "WHERE channel_topic_id = ? AND status NOT IN ('sent', 'cancelled')",
    (GEEK_TOPIC_ID,),
)
print(f"Cancelled {to_cancel} pending scheduled_messages rows targeting topic {GEEK_TOPIC_ID}")

# 2. Remove from verified_forum_topics
cur.execute("SELECT COUNT(*) FROM verified_forum_topics WHERE topic_id = ?", (GEEK_TOPIC_ID,))
verified_rows = cur.fetchone()[0]
cur.execute("DELETE FROM verified_forum_topics WHERE topic_id = ?", (GEEK_TOPIC_ID,))
print(f"Removed {verified_rows} verified_forum_topics row(s) for topic {GEEK_TOPIC_ID}")

# 3. Remove from forum_topics (auto-discovered, will repopulate if topic comes back)
cur.execute("SELECT COUNT(*) FROM forum_topics WHERE topic_id = ?", (GEEK_TOPIC_ID,))
forum_rows = cur.fetchone()[0]
cur.execute("DELETE FROM forum_topics WHERE topic_id = ?", (GEEK_TOPIC_ID,))
print(f"Removed {forum_rows} forum_topics row(s) for topic {GEEK_TOPIC_ID}")

# 4. Clean teaser_topic_ids in bot_message_routing (JSON array, may contain 442)
cur.execute("SELECT handler, teaser_topic_ids FROM bot_message_routing")
updated = 0
for row in cur.fetchall():
    raw = row["teaser_topic_ids"] or "[]"
    try:
        ids = json.loads(raw)
    except (TypeError, ValueError):
        continue
    if not isinstance(ids, list):
        continue
    if GEEK_TOPIC_ID in ids:
        new_ids = [i for i in ids if i != GEEK_TOPIC_ID]
        cur.execute(
            "UPDATE bot_message_routing SET teaser_topic_ids = ? WHERE handler = ?",
            (json.dumps(new_ids), row["handler"]),
        )
        updated += 1
        print(f"  Cleaned teaser_topic_ids for handler={row['handler']}: {ids} → {new_ids}")
print(f"Cleaned {updated} bot_message_routing row(s)")

# 5. Clear any rows where play_topic_id == 442 (should never be the case since 442 is geek not botson_corner, but be defensive)
cur.execute("SELECT COUNT(*) FROM bot_message_routing WHERE play_topic_id = ?", (GEEK_TOPIC_ID,))
play_rows = cur.fetchone()[0]
if play_rows:
    cur.execute("UPDATE bot_message_routing SET play_topic_id = NULL WHERE play_topic_id = ?", (GEEK_TOPIC_ID,))
    print(f"Cleared play_topic_id from {play_rows} bot_message_routing row(s)")

con.commit()
con.close()
print("\nDone.")
