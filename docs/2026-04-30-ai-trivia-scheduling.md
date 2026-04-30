# AI Trivia Scheduling Completion

Completed on 2026-04-30.

Summary:
- Scheduled trivia launcher rows use `message_type='trivia_round'` so the bot runs a live game instead of sending plain text.
- The real trivia game runs in the configured play topic, currently topic `4037` (`הפינה של בוטסון`).
- Scheduled trivia games now default to 10 questions unless an explicit count is provided.
- Each scheduled trivia game creates a linked editable draft announcement in topic `341` (`מצטרפים חדשים + עדכונים`) 4 hours before game time.
- Generated trivia questions now pass through a deterministic reviewer for structure, category fit, duplicates, and obvious low-quality patterns before being accepted.

Operational note:
- The pre-game announcement remains a draft so admins can edit or reschedule it before approving.
