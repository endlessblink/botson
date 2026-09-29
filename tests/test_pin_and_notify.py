"""auto_pin=2 pins with a member notification; 1/True stays a silent pin."""

from bot.handlers.calendar import PIN_AND_NOTIFY, pin_notifies_members


def test_only_value_two_notifies_members():
    assert PIN_AND_NOTIFY == 2
    assert pin_notifies_members(2)
    assert pin_notifies_members("2")
    for silent in (0, 1, True, False, None, "", "yes"):
        assert not pin_notifies_members(silent)


def test_both_pin_paths_use_the_shared_rule():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in ("bot/handlers/calendar.py", "dashboard/app.py"):
        source = (root / path).read_text(encoding="utf-8")
        assert "disable_notification=not pin_notifies_members(" in source, path
