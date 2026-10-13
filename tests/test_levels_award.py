import asyncio
from types import SimpleNamespace

from bot.database.db import Database
from bot.utils.levels import get_level
from dashboard import app as dash


def _req(body):
    async def j():
        return body
    return SimpleNamespace(session={"authenticated": True}, headers={}, json=j)


def test_there_are_fifty_levels_and_award_to_level_nine(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "l.db")); await db.init()
        await db.upsert_member(5, "a", "Her")
        out = await dash.award_member_points(_req({"user_id": 5, "to_level": 9}), db)
        top = await dash.levels_leaders(_req({}), db)
        await db.close()
        return out, top
    out, top = asyncio.run(scenario())
    assert out["level"] == 9 and out["granted"] == 1300
    assert top["leaders"][0]["level"] == 9
    assert get_level(10**6)["level"] == 50
