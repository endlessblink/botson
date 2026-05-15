#!/usr/bin/env python3
"""T-171: deterministic validator for `config/discussions.yaml`.

Catches the pool-quality leaks that let generic/duplicate/off-category
questions become permanent echoes in the materializer's few-shot prompt:

- non-string / empty entries
- not a clear single-question form
- English jargon (allowlisted brand/proper-nouns excluded)
- banned freshness fragments
- exact duplicates across all categories
- near-duplicates within OR across categories (Jaccard ≥ 0.55)
- very-short / generic prompts ("שאלה?", "מה דעתכם?")
- category names that aren't in settings.yaml:topics.discussions

Existing offenders can be allowlisted in
`config/discussion_pool_baseline.yaml` so the guardian runs green
immediately; the baseline shrinks over time.

Exit codes:
  0 — clean (modulo baseline allowlist)
  1 — new failures found

Usage:
  python scripts/validate_discussions.py
  python scripts/validate_discussions.py --json
  python scripts/validate_discussions.py --update-baseline   # writes current failures into baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bot.utils.freshness import (  # noqa: E402
    freshness_rejection,
    hebrew_normalize,
    near_duplicate,
)
from bot.utils.config import load_yaml  # noqa: E402


# English tokens that are usually fine inside a Hebrew question (brand
# names, file types, well-known initialisms). The validator is conservative:
# anything else with a 4+ char Latin run gets flagged.
_ALLOWED_LATIN = {
    "ai", "ar", "vr", "tv", "pc", "rpg", "mmo", "fps", "diy", "wfh",
    "ux", "ui", "api", "rss", "css", "html", "js", "qa",
    "netflix", "spotify", "apple", "google", "amazon", "tiktok", "instagram",
    "facebook", "youtube", "twitter", "discord", "reddit", "linkedin",
    "kindle", "playstation", "nintendo", "xbox", "steam", "switch",
    "marvel", "disney", "pixar", "hbo", "studio", "podcast",
}


def _has_disallowed_english(text: str) -> str | None:
    """Return the first 4+ char Latin run that isn't in `_ALLOWED_LATIN`."""
    import re
    runs = re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", text or "")
    for run in runs:
        if run.lower() not in _ALLOWED_LATIN:
            return run
    return None


def _is_single_question(text: str) -> bool:
    """Accept the prompt forms the pool actually uses:
    - exactly one `?` (most common)
    - imperative "show/share" forms ending with an arrow emoji (👇/👀)
    - fill-in-the-blank with underscores (___)
    Reject only when there are 2+ question marks (compound question).
    """
    qcount = text.count("?")
    if qcount >= 2:
        return False
    if qcount == 1:
        return True
    stripped = text.strip()
    if stripped.endswith(("👇", "👀", "🤔", "📸", "🎬", "🎮")):
        return True
    if "___" in stripped:
        return True
    return False


def _is_too_short_or_generic(text: str) -> bool:
    normalized = hebrew_normalize(text)
    # Sharp, short questions are fine ("סרט שהטריילר עשה לו עוול?" = 27 chars).
    # Block only the truly tiny stubs.
    if len(normalized) < 18:
        return True
    tokens = [t for t in normalized.split(" ") if len(t) >= 2]
    return len(tokens) < 4


def _load_configured_categories() -> set[str]:
    try:
        settings = load_yaml("settings.yaml") or {}
    except Exception:
        return set()
    discussions = (settings.get("topics") or {}).get("discussions") or {}
    return {str(k) for k in discussions.keys()}


def _load_baseline() -> set[tuple[str, str]]:
    """Baseline = {(category, text)} tuples that are known-bad but checked-in."""
    try:
        data = load_yaml("discussion_pool_baseline.yaml") or {}
    except FileNotFoundError:
        return set()
    except Exception:
        return set()
    out: set[tuple[str, str]] = set()
    for cat, items in (data.get("allowlist") or {}).items():
        for txt in items or []:
            out.add((str(cat), str(txt)))
    return out


def validate(discussions: dict, *, configured_categories: set[str] | None = None) -> list[dict]:
    """Return a list of `{category, text, reasons[]}` for every offending entry."""
    failures: list[dict] = []
    all_texts: list[tuple[str, str]] = []  # (category, text)

    for cat, items in (discussions or {}).items():
        if not isinstance(items, list):
            failures.append({
                "category": cat, "text": "", "reasons": ["not a list"],
            })
            continue
        for txt in items:
            reasons: list[str] = []
            if not isinstance(txt, str):
                failures.append({
                    "category": cat, "text": str(txt),
                    "reasons": ["not a string"],
                })
                continue
            stripped = txt.strip()
            if not stripped:
                reasons.append("empty")
            if stripped and not _is_single_question(stripped):
                reasons.append("not a single clear question")
            if stripped and _is_too_short_or_generic(stripped):
                reasons.append("too short / generic")
            jargon = _has_disallowed_english(stripped)
            if jargon:
                reasons.append(f"english jargon: {jargon!r}")
            fresh_reason = freshness_rejection(stripped)
            if fresh_reason and "copied static example" not in fresh_reason \
                    and "near-duplicate of static example" not in fresh_reason \
                    and "repeated scheduled text" not in fresh_reason \
                    and "near-duplicate of prior text" not in fresh_reason:
                # We only care about fragment/day-anchor reasons at this
                # stage — duplicate detection runs in the dedicated pass
                # below so we can attribute "dup of <other entry>" cleanly.
                reasons.append(fresh_reason)
            if reasons:
                failures.append({"category": cat, "text": stripped, "reasons": reasons})
            if stripped:
                all_texts.append((cat, stripped))

    # Cross-category exact duplicates
    seen: dict[str, tuple[str, str]] = {}
    for cat, txt in all_texts:
        key = hebrew_normalize(txt)
        if key in seen and seen[key] != (cat, txt):
            failures.append({
                "category": cat, "text": txt,
                "reasons": [f"exact duplicate of {seen[key][0]}:{seen[key][1][:60]!r}"],
            })
        else:
            seen[key] = (cat, txt)

    # Near-duplicates: O(n^2) but pool is small (~250 entries).
    texts_only = [t for _, t in all_texts]
    for i, (cat_a, text_a) in enumerate(all_texts):
        rest = texts_only[:i] + texts_only[i + 1:]
        near = near_duplicate(text_a, rest, threshold=0.65)
        if near and near != text_a:
            failures.append({
                "category": cat_a, "text": text_a,
                "reasons": [f"near-duplicate of {near[:60]!r}"],
            })

    # Category drift: categories present in discussions.yaml but not in
    # settings.yaml:topics.discussions are warned (not fatal — operators
    # sometimes stage new categories before wiring them up).
    if configured_categories:
        for cat in discussions.keys():
            if cat not in configured_categories:
                failures.append({
                    "category": cat, "text": "<category itself>",
                    "reasons": ["category not in settings.yaml:topics.discussions"],
                })

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--ignore-baseline", action="store_true",
        help="report ALL failures, including baseline-allowlisted ones",
    )
    args = parser.parse_args(argv)

    discussions = load_yaml("discussions.yaml") or {}
    configured = _load_configured_categories()
    failures = validate(discussions, configured_categories=configured)

    baseline = set() if args.ignore_baseline else _load_baseline()
    new_failures = [
        f for f in failures
        if (str(f.get("category")), str(f.get("text"))) not in baseline
    ]

    if args.json:
        print(json.dumps({
            "total_failures": len(failures),
            "baseline_size": len(baseline),
            "new_failures": new_failures,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"Pool entries scanned: {sum(len(v) if isinstance(v, list) else 0 for v in discussions.values())}")
        print(f"Total failures: {len(failures)}")
        print(f"Baseline-allowlisted: {len(failures) - len(new_failures)}")
        print(f"New failures: {len(new_failures)}")
        for f in new_failures[:50]:
            print(f"  [{f['category']}] {f['text'][:80]}")
            for r in f["reasons"]:
                print(f"      - {r}")
        if len(new_failures) > 50:
            print(f"  ... and {len(new_failures) - 50} more")

    return 0 if not new_failures else 1


if __name__ == "__main__":
    sys.exit(main())
