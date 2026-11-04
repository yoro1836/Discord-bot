"""페르소나 + 장기 기억. main: 공용 기억(주인에 대한 것, 전반적인 사실), user:<id>: 사람별 기억."""
import time

import aiosqlite

from . import config

DB = config.DATA_DIR / "bot.db"
MAX_NOTES = 50  # scope별 최대 개수 (넘으면 오래된 것부터 삭제)
MAX_NOTE_CHARS = 300
DEFAULT_PERSONA = "친근하고 솔직한 개인 비서. 반말/존댓말은 상대에 맞춤. 짧고 핵심적으로 답함."


async def init() -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, scope TEXT, name TEXT, content TEXT, ts REAL)"
        )
        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.commit()


def user_scope(user_id: int) -> str:
    return f"user:{user_id}"


async def get_persona() -> str:
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute("SELECT value FROM settings WHERE key = 'persona'")).fetchone()
    return row[0] if row else DEFAULT_PERSONA


async def set_persona(text: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings VALUES ('persona', ?)", (text,))
        await db.commit()


async def add(scope: str, content: str, name: str = "") -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO memories (scope, name, content, ts) VALUES (?, ?, ?, ?)",
            (scope, name, content[:MAX_NOTE_CHARS], time.time()),
        )
        await db.execute(
            "DELETE FROM memories WHERE scope = ? AND id NOT IN "
            "(SELECT id FROM memories WHERE scope = ? ORDER BY ts DESC LIMIT ?)",
            (scope, scope, MAX_NOTES),
        )
        await db.commit()
        return cur.lastrowid


async def update(mid: int, content: str, scopes: list[str] | None = None) -> bool:
    """scopes가 주어지면 그 안의 기억만 고칠 수 있음"""
    sql, args = "UPDATE memories SET content = ?, ts = ? WHERE id = ?", [content[:MAX_NOTE_CHARS], time.time(), mid]
    if scopes is not None:
        sql += f" AND scope IN ({','.join('?' * len(scopes))})"
        args += scopes
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(sql, args)
        await db.commit()
        return cur.rowcount > 0


async def delete(mid: int, scopes: list[str] | None = None) -> bool:
    """scopes가 주어지면 그 안의 기억만 지울 수 있음"""
    async with aiosqlite.connect(DB) as db:
        if scopes is None:
            cur = await db.execute("DELETE FROM memories WHERE id = ?", (mid,))
        else:
            q = ",".join("?" * len(scopes))
            cur = await db.execute(f"DELETE FROM memories WHERE id = ? AND scope IN ({q})", (mid, *scopes))
        await db.commit()
        return cur.rowcount > 0


async def notes(scope: str) -> list[tuple[int, str, str]]:
    async with aiosqlite.connect(DB) as db:
        return await (await db.execute(
            "SELECT id, name, content FROM memories WHERE scope = ? ORDER BY ts", (scope,)
        )).fetchall()


async def users() -> list[tuple[str, str, int]]:
    """(scope, 마지막 이름, 개수)"""
    async with aiosqlite.connect(DB) as db:
        return await (await db.execute(
            "SELECT scope, MAX(name), COUNT(*) FROM memories WHERE scope LIKE 'user:%' GROUP BY scope"
        )).fetchall()


async def clear(scope: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM memories WHERE scope = ?", (scope,))
        await db.commit()


async def context(user_id: int, user_name: str, owner: bool) -> str:
    """system prompt에 넣을 기억 블록"""
    main = await notes("main")
    mine = await notes(user_scope(user_id))
    out = ["## Main memory (shared)"]
    out += [f"- [{i}] {c}" for i, _, c in main] or ["(empty)"]
    who = "the owner" if owner else "this guest"
    out += [f"## About {who}: {user_name} (id {user_id})"]
    out += [f"- [{i}] {c}" for i, _, c in mine] or ["(nothing yet)"]
    return "\n".join(out)
