"""리마인더 저장/발송. 설정은 AI 도구(set_reminder)로만 함."""
import time

import aiosqlite
import discord
from discord.ext import commands, tasks

from .. import config

DB = config.DATA_DIR / "bot.db"


async def add(user_id: int, due: int, text: str) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO reminders (user_id, due, text) VALUES (?, ?, ?)", (user_id, due, text)
        )
        await db.commit()
        return cur.lastrowid


async def list_for(user_id: int) -> list[tuple[int, int, str]]:
    async with aiosqlite.connect(DB) as db:
        return await (await db.execute(
            "SELECT id, due, text FROM reminders WHERE user_id = ? ORDER BY due", (user_id,)
        )).fetchall()


async def delete(user_id: int, rid: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (rid, user_id))
        await db.commit()
        return cur.rowcount > 0


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS reminders ("
                "id INTEGER PRIMARY KEY, user_id INTEGER, due INTEGER, text TEXT)"
            )
            await db.commit()
        self.check.start()

    async def cog_unload(self) -> None:
        self.check.cancel()

    @tasks.loop(seconds=15)
    async def check(self) -> None:
        now = int(time.time())
        async with aiosqlite.connect(DB) as db:
            rows = await (await db.execute(
                "SELECT id, user_id, text FROM reminders WHERE due <= ?", (now,)
            )).fetchall()
            for rid, uid, text in rows:
                try:
                    user = await self.bot.fetch_user(uid)
                    await user.send(f"⏰ {text}")
                except discord.HTTPException:
                    continue  # 다음 루프에서 재시도
                await db.execute("DELETE FROM reminders WHERE id = ?", (rid,))
            await db.commit()

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
