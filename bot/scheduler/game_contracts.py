"""Executable-game dispatch contract.

Scheduled games are not plain scheduled text. They must run through a real
game launcher, return the Telegram message_id that represents a successful
launch, claim their calendar slot, support test-vs-main routing, and share the
warm-up RSVP/orphan-game guards when a warmup_marker is present.

Add every new scheduled executable game here before wiring Populate, send-now,
or calendar dispatch. The guardian in tests/test_game_dispatch_contract.py
fails when a contracted game is missing the expected dispatcher/send-now hooks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutableGameContract:
    message_type: str
    scheduler_handler_symbol: str
    send_now_handler_symbol: str
    subject_marker_keys: tuple[str, ...]
    supports_test_target: bool = True
    requires_launch_message_id: bool = True
    claims_calendar_slot: bool = True
    uses_warmup_guards: bool = True


EXECUTABLE_GAME_CONTRACTS: dict[str, ExecutableGameContract] = {
    "trivia_round": ExecutableGameContract(
        message_type="trivia_round",
        scheduler_handler_symbol="start_scheduled_trivia_round",
        send_now_handler_symbol="start_scheduled_trivia_round",
        subject_marker_keys=("categories",),
    ),
    "emoji_puzzle": ExecutableGameContract(
        message_type="emoji_puzzle",
        scheduler_handler_symbol="start_emoji_night",
        send_now_handler_symbol="start_emoji_night",
        subject_marker_keys=("media_type",),
    ),
}

EXECUTABLE_GAME_TYPES = frozenset(EXECUTABLE_GAME_CONTRACTS)

# Companion rows are not game launches, but they are time-bound game lifecycle
# rows and must claim their exact scheduled slot with the game rows.
GAME_COMPANION_MESSAGE_TYPES = frozenset({
    "trivia_warmup_rsvp",
    "warmup_reminder",
})

GAME_SLOT_CLAIMING_TYPES = EXECUTABLE_GAME_TYPES | GAME_COMPANION_MESSAGE_TYPES
