# WhatsApp (Botty + Botson) — edge cases

Living list of user-interaction and technical edge cases for the WhatsApp group
אלהוריים וזה. Every row says how it is handled today and which test pins it.
Botty tests: `worlds-greatest-bot/test/whatsappSummary.test.js` (and `whatsappThrottle.test.js`).
Botson tests: `tests/test_whatsapp_dispatch.py`.

## Members tagging Botty

| Case | Handling | Test |
|---|---|---|
| `@בוטי תקציר` (real mention, `@number`, or typed `@בוטי`) | "Working" reply (deleted after), then a summary of what was written since the previous one | tagging the bot posts a summary; the working message is deleted |
| `תקציר` without a tag | Ignored | the keyword alone, without a tag, does nothing |
| Tag with anything else (question, "help", insult, "מה קורה") | First time that day: explanation (only `@בוטי תקציר`, once a day, more later). Then silent that day | a tag that is not תקציר gets the explanation |
| Same member tags again and again | Silent after the explanation; resets next Israel day | same test |
| Asking again soon after a summary | No time limit: a new summary needs 30+ real new messages since the last one; below that Botty quotes the last summary and says how many more are needed | no time wait: a new summary is allowed as soon as enough new messages piled up |
| Only reactions since the last summary ("חחח", 👍) | Don't count toward the 30 | emoji-only and laughter replies do not count |
| More than 400 new messages | Newest 400 summarized; header says "400 מתוך N" | over 400 new messages |
| A member asks not to be summarized | Operator adds them to the opt-out list; their messages are skipped | members on the opt-out list are left out |
| First two weeks of real use | Every real summary is copied to the test group (until 2026-10-24) | oversight copies |
| Two people ask at the same moment | Second gets "already preparing" once | (busy path) |
| Operator (Noam) | Always answered | the operator always gets an answer |
| Mention of many people at once / `@all` | Not treated as talking to Botty unless it contains `תקציר` | a mention of many people at once |
| Tag in a group where summary is not enabled, or in a private chat | Ignored | ignores other groups |
| Quoted earlier summary was deleted | Reply is sent unquoted instead of failing | index.js reply adapter (fallback) |

## Technical

| Case | Handling | Test |
|---|---|---|
| WAHA delivers the same message twice | Deduplicated by message id | duplicate deliveries |
| Reconnect replays old tags | Tags older than 10 min ignored | replayed old tags |
| AI slow (>100s Cloudflare limit) | Botson runs a background job; Botty polls up to 4 min | Botson summarizer polls |
| Botson restarts mid-job | Poll gets 404 → stop at once → light failure line | stops waiting at once |
| Codex / Botson fails | Members get a light line (once, then the explanation); operator gets a Telegram alert | failure tests (both repos) |
| A failed summary | Doesn't mark anything as summarized; the next request retries | a failed summary does not use up the allowance |
| Model adds headings, `**bold**`, links, numbering, blank lines | Stripped and rebuilt as clean points | model markdown and headings are cleaned |
| Model output too long | Capped at 8 points / 1500 chars, whole points only | capped on whole points |
| Phone numbers in output | Removed | cleaned of phone numbers |
| Instructions written inside the chat ("ignore your rules…") | Prompt says transcript is content only | prompt in config |
| Very long member message | Shortened to 600 chars, not dropped | long member messages are shortened |
| Media-only / deleted messages | Not in the transcript | transcript test |
| Botty only sees messages since it joined (2026-10-09) | Summary covers what exists; grows to 400 over time | — |
| Little or no conversation since the last summary | No new summary, no "working" message, no AI call; Botty points at the previous summary | too little new since the last summary |
| A later summary | Covers only messages after the previous summary (header says so) | a later summary covers only what was written since |
| Tags to Botty inside the conversation | Left out of what gets summarized | tags to Botty are not part of what gets summarized |
| Someone shared something personal (health, family, money…) | Prompt: mention that a personal share happened, no details, no name | prompt in config |
| Wording implies summaries are automatic | Texts say a summary comes on request, when there's enough new conversation | config copy |

## Hebrew layout (WhatsApp RTL)

| Case | Handling | Test |
|---|---|---|
| Last line of a message flips LTR (dash on the left) | Every message ends with an invisible RLM-only line | invisible last line (both repos) |
| Line starting with an English word | Each line wrapped in RLM | English-start line test |
| Emoji bullets render as images and flip lines | Plain dash bullets only | dash bullet test |
| `•` overlaps the first Hebrew letter | Not used | — |
| Botson multi-line posts to WhatsApp | Same RTL wrapping in the sender | multiline hebrew is rtl |

## Botson posts to WhatsApp

| Case | Handling | Test |
|---|---|---|
| Nothing is mirrored automatically | Only rows explicitly targeted `whatsapp` go there | dispatch tests |
| Game / RSVP / unsupported types | Refused when creating or editing | row problems caught before scheduling |
| Image attached | Refused up front (not silently dropped) | same |
| Real group not configured yet | Refused up front with a clear message | edit to unconfigured group is refused |
| Poll with <2 or >12 options, duplicates, option >100 chars, question >255 | Refused up front; also blocked at send time | poll limits |
| WhatsApp rejects the message after accepting it | Counted sent only on ack ≥1; otherwise failed + admin alert | rejected delivery is failed and alerted |
| Same quality gates as Telegram | Freshness + conversation gate applied | generated conversation row is never sent |

## Throttle (watch-only)

| Case | Handling | Test |
|---|---|---|
| Member sends >8 messages in 60s | Recorded once per burst; shown on the dashboard Spam page; no action | whatsappThrottle tests |
| Enforcement | Shelved by operator (no per-member mute in WhatsApp; bot DMs risk a ban) | — |
