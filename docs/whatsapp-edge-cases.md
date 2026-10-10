# WhatsApp (Botty + Botson) — edge cases

Living list of user-interaction and technical edge cases for the WhatsApp group
אלהוריים וזה. Every row says how it is handled today and which test pins it.
Botty tests: `worlds-greatest-bot/test/whatsappSummary.test.js` (and `whatsappThrottle.test.js`).
Botson tests: `tests/test_whatsapp_dispatch.py`.

## Members tagging Botty

| Case | Handling | Test |
|---|---|---|
| `@בוטי תקציר` (real mention, `@number`, or typed `@בוטי`) | Immediate "working" reply, then summary of last 400 messages | tagging the bot posts a summary; mention detection |
| `תקציר` without a tag | Ignored | the keyword alone, without a tag, does nothing |
| Tag with anything else (question, "help", insult, "מה קורה") | First time that day: explanation (only `@בוטי תקציר`, once a day, more later). Then silent that day | a tag that is not תקציר gets the explanation |
| Same member tags again and again | Silent after the explanation; resets next Israel day | same test |
| Many members ask for a summary within 3h | One new summary per group per 3h; others get one pointer to the latest summary, then the explanation, then silence | one summary per group per 3 hours |
| Two people ask at the same moment | Second gets "already preparing" once | (busy path) |
| Operator (Noam) | Always answered; can re-run inside the 3h window | operator can ask again / always gets an answer |
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
| A failed summary | Does not start the 3h cooldown | a failed summary does not use up the allowance |
| Model adds headings, `**bold**`, links, numbering, blank lines | Stripped and rebuilt as clean points | model markdown and headings are cleaned |
| Model output too long | Capped at 8 points / 1500 chars, whole points only | capped on whole points |
| Phone numbers in output | Removed | cleaned of phone numbers |
| Instructions written inside the chat ("ignore your rules…") | Prompt says transcript is content only | prompt in config |
| Very long member message | Shortened to 600 chars, not dropped | long member messages are shortened |
| Media-only / deleted messages | Not in the transcript | transcript test |
| Botty only sees messages since it joined (2026-10-09) | Summary covers what exists; grows to 400 over time | — |

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
