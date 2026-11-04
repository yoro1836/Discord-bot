"""할 일 목록 (사람별)."""
import time

import aiosqlite

from . import config

DB = config.DATA_DIR / "bot.db"


async def init() -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT, "
            "due TEXT, done INTEGER DEFAULT 0, ts REAL)"
        )
        await db.commit()


async def add(user_id: int, text: str, due: str | None) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO todos (user_id, text, due, ts) VALUES (?, ?, ?, ?)", (user_id, text, due, time.time())
        )
        await db.commit()
        return cur.lastrowid


async def list_for(user_id: int, include_done: bool = False) -> list[dict]:
    q = "SELECT id, text, due, done FROM todos WHERE user_id = ?" + ("" if include_done else " AND done = 0")
    async with aiosqlite.connect(DB) as db:
        rows = await (await db.execute(q + " ORDER BY done, due IS NULL, due, id", (user_id,))).fetchall()
    return [{"id": i, "text": t, "due": d, "done": bool(x)} for i, t, d, x in rows]


async def set_done(user_id: int, tid: int, done: bool = True) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("UPDATE todos SET done = ? WHERE id = ? AND user_id = ?", (int(done), tid, user_id))
        await db.commit()
        return cur.rowcount > 0


async def edit(user_id: int, tid: int, text: str | None, due: str | None) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "UPDATE todos SET text = COALESCE(?, text), due = COALESCE(?, due) WHERE id = ? AND user_id = ?",
            (text, due, tid, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete(user_id: int, tid: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("DELETE FROM todos WHERE id = ? AND user_id = ?", (tid, user_id))
        await db.commit()
        return cur.rowcount > 0
