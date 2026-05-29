"""Guardian for adding new scheduled executable games.

The scheduler has several generic protections before the per-type branch:
dispatch claiming, stale-row dropping, target validation, slot claiming, and
real message_id marking. A future game only inherits that reliability if it is
declared as an executable game and wired through the same branch structure.
"""

import inspect
import unittest

from bot.handlers import calendar
from bot.scheduler.dispatch_owner import CALENDAR_OWNED_TYPES
from bot.scheduler.game_contracts import (
    EXECUTABLE_GAME_CONTRACTS,
    EXECUTABLE_GAME_TYPES,
    GAME_SLOT_CLAIMING_TYPES,
)


class GameDispatchContractTests(unittest.TestCase):
    def test_current_games_are_declared_as_executable_games(self):
        self.assertTrue({"trivia_round", "emoji_puzzle"} <= EXECUTABLE_GAME_TYPES)

    def test_executable_games_are_calendar_owned_and_slot_claiming(self):
        for message_type, contract in EXECUTABLE_GAME_CONTRACTS.items():
            with self.subTest(message_type=message_type):
                self.assertIn(message_type, CALENDAR_OWNED_TYPES)
                self.assertIn(message_type, GAME_SLOT_CLAIMING_TYPES)
                self.assertTrue(contract.supports_test_target)
                self.assertTrue(contract.requires_launch_message_id)
                self.assertTrue(contract.claims_calendar_slot)
                self.assertTrue(contract.uses_warmup_guards)

    def test_calendar_dispatch_has_explicit_branch_for_every_game_contract(self):
        source = inspect.getsource(calendar.check_and_send_due_messages)
        for message_type, contract in EXECUTABLE_GAME_CONTRACTS.items():
            with self.subTest(message_type=message_type):
                self.assertIn(
                    f'msg.get("message_type") == "{message_type}"',
                    source,
                    f"{message_type} must have an explicit calendar branch; "
                    "falling through to plain text send is not a game launch.",
                )
                self.assertIn(
                    contract.scheduler_handler_symbol,
                    source,
                    f"{message_type} calendar branch must call its launcher.",
                )
                self.assertIn(
                    "_require_message_id",
                    source,
                    "game launch success must be tied to a real Telegram message_id.",
                )

    def test_send_now_has_explicit_branch_for_every_game_contract(self):
        from dashboard import app as dashboard_app

        source = inspect.getsource(dashboard_app._send_scheduled_row)
        for message_type, contract in EXECUTABLE_GAME_CONTRACTS.items():
            with self.subTest(message_type=message_type):
                self.assertIn(
                    f'msg.get("message_type") == "{message_type}"',
                    source,
                    f"{message_type} must have an explicit send-now branch.",
                )
                self.assertIn(
                    contract.send_now_handler_symbol,
                    source,
                    f"{message_type} send-now branch must call its launcher.",
                )


if __name__ == "__main__":
    unittest.main()
