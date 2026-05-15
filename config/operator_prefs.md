---
name: noam-personal-preferences
description: Noam's personal working preferences — design taste, motion grammar, copy voice, workflow patterns, **and Hebrew content rules for the Botson Telegram bot's discussion/morning/evening generation**. Load proactively at the start of any task involving design, video, motion, Hebrew content, or anything where Noam's specific aesthetic / process preferences would change the output. Cross-project, cross-tool (Claude Code, OpenCode, Codex). Rules are added as Noam corrects work — each correction becomes a persistent rule with context for when it applies. **Canonical location: `config/operator_prefs.md` in the botson repo. The skill entry at `~/.codex/skills/noam-personal-preferences/SKILL.md` is a symlink to this file.**
metadata:
  short-description: Canonical operator preferences — design, motion, voice, workflow, Hebrew content
  owner: Noam
  created: 2026-05-15
  canonical_path: config/operator_prefs.md
  read_by: [Claude Code, Codex CLI, OpenCode, Botson bot (Hebrew content rules section only via /api/operator-prefs/hebrew)]
---

# Noam's Personal Preferences — Canonical Store

This file is the **single source of truth** for operator preferences across every agent and runtime that touches Noam's work. There is no other store. If you find a rule restated elsewhere (CLAUDE.md, a skill, a SQLite table) — that's a duplicate; this file wins.

## How this file is maintained

- Every time Noam corrects a design / motion / copy / Hebrew-content choice, the agent extracts the rule and adds it here.
- Each rule has: short name → rule statement → **Context:** → **Why:** → **Source:** (which build / conversation / commit it came from).
- If a new correction contradicts an existing rule, **update the existing rule** (don't append). Note the date of the change.
- If Noam says "actually I prefer the opposite now" — replace, don't accumulate stale versions.
- Periodically (every ~10 rules), the agent should ask Noam to review accumulated rules and prune the dead ones.

## How writes happen (no silent inference)

Every write requires an explicit operator-approved action:

1. **`/teach-bot <text>` skill** — Noam (or the agent acting under Noam's instruction in chat) appends a rule with citation to the current conversation.
2. **Dashboard proposal banner** — when ≥5 new content rejections accumulate (planner deny, qa-scoring score 1-2, etc.), the dashboard shows a one-click "approve this proposed rule" banner. Click approve → rule lands here with citation to the source feedback ids.
3. **Direct markdown edit** — Noam can always open this file and edit. Re-read happens on next mtime change.
4. **`/untrain-bot <substring>` skill** — operator-triggered removal of any rule line matching the substring, with confirmation.

**The bot does NOT auto-promote rules from feedback without operator approval** (the silent N=5 auto-promote that existed briefly was reversed on 2026-05-16; research consensus is against it — see plan file).

## How reads happen (no synced copies)

- **Bot runtime** (Botson dashboard, `dashboard/app.py`): reads only the `### Hebrew content rules` section, via `GET /api/operator-prefs/hebrew`. 60-second mtime-based cache. No copy into SQLite.
- **Claude Code / Codex / OpenCode**: the skill auto-loads via standard skill discovery at session start. The symlink at `~/.codex/skills/noam-personal-preferences/SKILL.md` makes that work.
- **Hermes**: references this file path; does not restate rules.

If you're an AI agent debugging "why is the output wrong" — this file is the only place to look for learned soft rules. Hard rules (immutable spec) live in `config/question_quality.md`. Runtime configs (schedule, toggles, topic ids) live in `config/settings.yaml` and friends.

---

## Rule taxonomy

| Context | What it covers |
|---|---|
| **Hebrew content rules** | Discussion/morning/evening prompt guidance for the Botson Telegram bot. Read by the bot's prompt builder via `/api/operator-prefs/hebrew`. |
| **Motion grammar** | Durations, easings, transitions, camera moves, "feels too fast / too slow / too springy" |
| **Visual style** | Colors, typography, weight, contrast, density, photographic style |
| **Hebrew/RTL design** | How Hebrew text should look, paired with Latin, line breaks, kerning |
| **Copy/voice** | Tone, word choice, length, what's marketing-speak vs not |
| **Workflow** | How Noam wants to be interviewed, when to ship vs iterate, when to use which tool |
| **Aesthetic loyalty** | Which brand constraints are real ("must match site brand") vs default ("I was being conservative") |
| **AI collaboration** | How Noam expects an AI to evaluate its own work, what counts as "done" |

If a correction doesn't fit any of these, add a new context category.

---

## Rules (newest at top within each section)

### Hebrew content rules

*Read by the Botson bot's prompt builder. Each line is injected into every Hebrew-content LLM prompt.*

- האיכות נמדדת קודם בעיני המפעיל, לא לפי ההשתתפות בקבוצה. אם המפעיל לא היה שולח את זה — אל תייצר את זה.
- כל שאלה חייבת לעבור את שני המבחנים: ספציפיות (עוגן לערוץ) ורוחב תחולה (לפחות 5 מ-10 קוראים יוכלו לענות מהזיכרון, בלי להמציא).
- אם רעיון נדחה פעם אחת בקטגוריה — אל תייצר אותו שוב, גם לא בנוסח שונה (פאראפראזה נחשבת חזרה).
- מגוון תבניות הוא חובה ולא המלצה. אותה תבנית פעמיים ברצף = פסילה אוטומטית.
- שאלות חכמות > שאלות פילר רגשי. עוגן קונקרטי (חפץ, פעולה, החלטה, רגע) > קופי פואטי.
- "איך היה היום" / "מה הדבר הטוב היום" / "הריטואל שסוגר" — פסולים גם אם נראים תמימים, כי הם פילר.
- שאלת מאמץ (פסקה, רשימה, הסבר) פסולה. תמיד בקש פרט אחד, שם אחד, החלטה אחת.
- עברית של חבר בקבוצה, לא של קופירייטר. אם זה נשמע כמו תרגום מ-engagement prompt באנגלית — לפסול ולנסח שוב.

**Source:** Botson unification thread, 2026-05-15 — operator stated principles distilled from prior conversations about why discussion prompts were unsatisfactory.

### Motion grammar

#### Per-shot duration in multi-shot motion design: 3–5 seconds
When a motion piece is split into multiple visual shots (type-as-hero → macro close-up → wide reveal, etc.), each individual shot needs **3–5 seconds** to land. Shorter and the viewer doesn't process the shot's content before the cut — reads as rushed / "ad-fast" rather than confident.
- **Context:** Any multi-shot motion design — product demo, feature reveal, brand reel.
- **Why:** Noam said v8 (4 shots in 4.2s = ~1s per shot) "is too fast." The cure isn't to go back to one slower shot — keep the multi-shot structure but give each shot proper time.
- **How to apply:** Total duration target ≈ `(number_of_shots) × 3.5s`. So 4 shots ≈ 14s, 5 shots ≈ 17s, 3 shots ≈ 10s. Within each shot, action takes the first 2–3s, then 0.5–1.5s of ambient/secondary motion. **Never cut earlier than 2.5s into a shot** unless it's a deliberate snap-cut for rhythm.
- **Source:** v8 prototype, 2026-05-16 — Noam: "this is better, but it is too fast, add to my style skill — things need to be around 3-5 a shot if you split the shots."

#### Single-shot static-UI compositions are boring regardless of polish
A motion design clip that consists of ONE fixed camera looking at ONE UI mock — even with cursors, particles, glows, elegant button-morphs — reads as boring. Polish doesn't rescue a single-shot concept. Reference videos (TimeFrame SaaS demo, Linear changelog reels) achieve excitement through **visual variety beat-to-beat**: multiple camera positions, type-as-hero moments, depth/perspective shifts, scene cuts to different visual spaces, 3D objects, bento grids parallaxing.
- **Context:** Any product-demo / feature-reveal video, especially short (≤10s) ones.
- **Why:** Noam: "this is a boring way to pass a concept" — pointing at structural sameness, not missing polish.
- **How to apply:** A short product-demo should have AT LEAST 3 distinct visual beats / shots. Examples: big kinetic typography (no UI), 3D-tilted product mockup, macro close-up of one detail, bento-grid with parallax, wide reveal pulling back to context. If you can only think of one approach, the concept is too narrow — pitch alternatives before building.
- **Source:** v6/v7 prototypes, 2026-05-15 — Noam: "and this is a boring way to pass a concept."

#### No dead time — every second needs visible motion
A 6-second clip with 3 seconds of "hold" at the end is half-broken. Every second must contain at least one visible change — primary action, secondary ambient, camera push, glow shift, type animation. Long unchanging holds read as "the video ended but didn't end."
- **Context:** Any motion design / product-demo / promo video.
- **Why:** Noam: "a lot of boring time when nothing happens visually."
- **How to apply:** (1) Cut total duration to match the action. (2) During cursor approach / before clicks, give the target element a soft pulse. (3) After the main action, layer continuous secondary motion (slow camera push-in, glow halo, ambient particle drift). (4) Avoid `tl.to({}, { duration: X.X })` holds — something earlier should extend instead.
- **Source:** v6 prototype, 2026-05-15 — Noam: "a lot of boring time when nothing happens visually."

#### For UI animations, show a real cursor — not abstract pulse/glow
When demonstrating a click in a product-demo, render an actual cursor SVG that travels to the target and clicks. A coral pulse-ring or "glow at the click point" reads as lazy stand-in.
- **Context:** Any product-demo or feature-reveal video showing a UI interaction.
- **Why:** Noam: "we need an actual cursor animation entering and clicking this button not just the effect."
- **How to apply:** SVG cursor (Mac-style pointer arrow + drop-shadow). Enters from off-screen, travels with `power3.out` over ~0.7–1.2s, compresses on click (`scale 0.82` for 100ms), small white click-ring expands at the contact point, then exits.
- **Source:** v6 prototype, 2026-05-15 — Noam: "we need an actual cursor animation entering and clicking this button not just the effect."

#### Source-to-destination matched-element transitions, not fade-out + fade-in
When a UI element transforms (button → chip, card → tile), the SAME DOM element should travel from source to destination via transform — OR a connecting visual (particle burst flying source→destination) should link the two states. NOT fade source out + fade destination in at a different position with nothing connecting them.
- **Context:** Any "this becomes that" interaction in a UI demo.
- **Why:** Noam: "this transition is super low quality and lazy" (about fade-only chip-arrival).
- **How to apply:** Either (a) one DOM element animated with `padding`/`font-size`/`box-shadow`/`transform`, or (b) source collapses + emits particles that fly to destination + destination materializes from particle arrival. The visual MUST connect source to destination.
- **Source:** v6 prototype, 2026-05-15 — Noam: "this transition is super low quality and lazy."

### Visual style

#### Type for 1920×1080 video is 1.5–2× browser scale
What looks fine at 16px on a browser at 1× device pixel ratio looks tiny when the same 1920×1080 frame is rendered as a video file viewed at fit-to-screen. Default video-scale for body text: 22–28px. Card titles: 48–56px. Headlines: 64–80px. Days-left counters or hero numerals: 72–100px+.
- **Context:** Any motion design / HyperFrames / Remotion composition at 1920×1080.
- **Why:** Noam: "text is not only blurry but small."
- **How to apply:** Multiply typical web-UI font sizes by ~1.5–1.8× for video output. Heavy weights (600–800) for headlines. Reserve 13–16px only for true mono labels / kickers; bump letter-spacing tighter (0.18–0.24em, not 0.32em+).
- **Source:** v6 prototype, 2026-05-15 — Noam: "text is not only blurry but small."

#### Fill the frame — no large dead space around the composition
A 1920×1080 frame should be ~75–85% occupied by content. A small element centered with vast negative space reads as unfinished, not minimal. Reference (TimeFrame SaaS demo) has hero elements occupying ~80% of the frame.
- **Context:** Motion design / product-demo videos at 1920×1080.
- **Why:** Noam circled empty area: "this is just empty dead space."
- **How to apply:** Hero/foreground element width ≥75% of frame width (≥1440px on 1920). Stack headline + element + caption to use full vertical range. Single-element compositions: make it BIG, fill 80%.
- **Source:** v6 prototype, 2026-05-15.

#### Sharpness: no backdrop-filter blur on text-bearing surfaces, no grain on motion
`backdrop-filter: blur(...)` softens everything that sits on the element, including foreground text. Grain overlays with `mix-blend-mode: overlay` add micro-noise that reads as "the whole image is fuzzy."
- **Context:** Glassmorphic UI panels, browser mocks, modals — anything with text on top.
- **Why:** Noam: "text is not sharp, actually nothing is sharp."
- **How to apply:** For "glassy" feel without softness, use `background: rgba(22, 14, 38, 0.92)` (solid translucent ~90%+) + 1px hairline border + tasteful box-shadow. No `backdrop-filter`. If grain is wanted, ≤3% opacity and `mix-blend-mode: multiply` (not `overlay`).
- **Source:** v6 prototype, 2026-05-15 — Noam: "text is not sharp, and is too close to the card, actually nothing is sharp."

#### No gradient-clipped text on headlines
`background-clip: text` with a gradient and `color: transparent` renders soft on many displays — gradient interpolation creates sub-pixel anti-aliasing that reads as "fuzzy." For accent words, use a solid color and bump the font-weight by one step.
- **Context:** Any kinetic-typography or headline with an "accent word."
- **Why:** Same "not sharp" remark in v6.
- **How to apply:** `color: #ff7a5c; font-weight: 800;` instead of `background-clip: text` with gradients.
- **Source:** v6 prototype, 2026-05-15.

#### Keep breathing room between caption text and adjacent UI
A headline / caption above a UI panel needs ≥80px vertical gap. Anything closer reads as "crowded" and the eye doesn't separate the two pieces.
- **Context:** Caption-above-element compositions; floating labels next to product mockups.
- **Why:** Noam: "text is too close to the card."
- **How to apply:** Caption `top: 36–60px`; element `top: 50%` or lower so its top edge is ≥80px below the caption's bottom edge.
- **Source:** v6 prototype, 2026-05-15.

### Hebrew/RTL design

*(No rules captured yet.)*

### Copy/voice

#### On-screen text must be clear to someone who doesn't already know the product
Kinetic-typography moments where a user-who-doesn't-know-the-product sees three abstract words (`סמן · הוגש · נסגר`) and has no idea what the feature is = the type failed. Words on screen need to land a complete concept, not act as design ornament.
- **Context:** Any kinetic-typography hero shot, big-type beat, or text overlay in a product video.
- **Why:** Noam: "here the text is unclear. really unclear what this is for a user that doesnt know" — pointing at three isolated Hebrew words.
- **How to apply:** Prefer a **short clear phrase** that names cause→effect over a list of isolated words. Example that works: `מסמן "הוגש"? הקול עוזב את הקטלוג` (Mark "submitted"? The call leaves the catalogue). Two lines max, ~4–7 words total. **Test:** if you removed all brand context, would a stranger understand what just happened? If no, rewrite.
- **Source:** v8 prototype, 2026-05-16 — Noam: "here the text is unclear. really unclear what this is for a user that doesnt know."

#### Text on screen: clear but not too long
Two competing constraints: text must communicate enough to be understood without prior context, AND text must be short enough to read in the time it's on screen. The intersection is **one short clear phrase per beat** — never a paragraph, never a single ambiguous word.
- **Context:** Captions, hero typography, kinetic-text moments, video overlays.
- **Why:** Noam: "text needs to be clear but not too long."
- **How to apply:** Aim for **4–8 words per beat**. Hebrew: same range. If a beat needs more, split it into two consecutive beats with shorter phrases each. Headlines: 2–6 words. Captions: ≤8 words. Mono kickers / labels: 1–3 words.
- **Source:** v8 prototype, 2026-05-16 — Noam: "text needs to be clear but not too long."

### Workflow

*(No rules captured yet.)*

### Aesthetic loyalty

*(No rules captured yet.)*

### AI collaboration

*(No rules captured yet.)*

---

## Citations format

When citing the source of a rule:

```
**Source:** v6 prototype, 2026-05-15 — Noam: "the coral is too saturated, dial it back."
**Source:** Botson dashboard rejection batch [42, 43, 44, 45, 46], 2026-05-20
**Source:** Botson chat via /teach-bot, 2026-05-18 — Noam: "stop using the word 'ריטואל' in singles questions."
```

This lets future-me trace the rule back to a real moment, not an inferred preference.

---

## How to apply this file at session start

1. **At task start**: read this entire file (it's short — that's intentional). For Hebrew-content tasks, the bot already auto-injects the Hebrew section into prompts.
2. **Before making a stylistic choice**, check if any rule applies. Cite the rule in code comments or design notes.
3. **If Noam corrects you**, the immediate fix is *implementing the change*. The persistent fix is **adding a rule here** so the next session doesn't make the same mistake.

The point is to compound: Noam should never have to give the same correction twice across sessions, agents, or tools.
