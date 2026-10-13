"""Engagement verdict is advisory, config-driven, and needs enough sends."""
from pathlib import Path

import yaml

from dashboard.app import _engagement_verdict

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["engagement_verdict"]


def test_too_few_sends_is_watch():
    assert _engagement_verdict({"sent": 2, "per_post": 0.0}, CFG) == "watch"


def test_dead_slot_with_enough_sends_is_pause():
    assert _engagement_verdict({"sent": 5, "per_post": 0.2}, CFG) == "pause"


def test_landing_slot_is_keep():
    assert _engagement_verdict({"sent": 5, "per_post": 2.0}, CFG) == "keep"


def test_middle_is_watch():
    assert _engagement_verdict({"sent": 5, "per_post": 1.0}, CFG) == "watch"


def test_cutoffs_come_from_config():
    assert _engagement_verdict({"sent": 5, "per_post": 3.0}, {"keep_at_least": 5}) == "watch"


def test_every_verdict_has_copy():
    for v in ("keep", "watch", "pause"):
        assert CFG["labels"][v] and CFG["tooltips"][v]
