"""대화가 잠잠해지면 뒤에서 따로 돌면서 기억을 정리 (저장/수정/삭제/중복 합치기)."""
import asyncio
import logging
import os

import aiohttp

from . import aitools, config, memory
from .chatgpt import ChatGPT

log = logging.getLogger(__name__)
ENABLED = os.environ.get("AI_MEMORY_AUTO", "0") == "1"  # 기본은 대화 중 GPT가 직접 관리
IDLE_SECONDS = int(os.environ.get("AI_MEMORY_IDLE", 90))  # 마지막 대화 후 이만큼 조용하면 정리
MAX_BUFFER = 8  # 이만큼 쌓이면 바로 정리
TOOLS = ("remember_user", "remember_main", "update_memory", "forget_memory")

PROMPT = """You maintain the long-term memory of a Discord AI assistant. You do not talk to anyone.
Below are the current memories and recent exchanges with one person ({who}: {name}, id {uid}).
Update memory using the tools, then stop:
- Save lasting facts: preferences, background, relationships, ongoing plans, how they like answers.
- If a fact changed, update_memory the old entry instead of adding a new one.
- Merge duplicates (update one, forget the rest); forget things that are clearly wrong or obsolete.
- Skip small talk, one-off questions, and anything secret (passwords, tokens, private keys).
- {main_rule}
- Notes are short, in the conversation's language, and written as facts ("좋아하는 음식: 마라탕").
If nothing needs changing, call no tools.

{memories}

# Recent exchanges
{log}"""


class Curator:
    def __init__(self, gpt: ChatGPT, http_getter, model_getter) -> None:
        self.gpt = gpt
        self.http = http_getter
        self.model = model_getter
        self.buffers: dict[int, list[str]] = {}
        self.names: dict[int, str] = {}
        self.timers: dict[int, asyncio.Task] = {}

    def note(self, user_id: int, user_name: str, prompt: str, reply: str) -> None:
        if not ENABLED or not self.gpt.logged_in:
            return
        buf = self.buffers.setdefault(user_id, [])
        buf.append(f"{user_name}: {prompt[:2000]}\nAssistant: {reply[:2000]}")
        self.names[user_id] = user_name
        if t := self.timers.get(user_id):
            t.cancel()
        delay = 0 if len(buf) >= MAX_BUFFER else IDLE_SECONDS
        self.timers[user_id] = asyncio.create_task(self._later(user_id, delay))

    async def _later(self, user_id: int, delay: int) -> None:
        await asyncio.sleep(delay)
        self.timers.pop(user_id, None)
        exchanges = self.buffers.pop(user_id, [])
        if exchanges:
            try:
                await self.run(user_id, self.names.get(user_id, ""), exchanges)
            except Exception:  # noqa: BLE001 - 정리는 실패해도 대화엔 영향 없음
                log.exception("memory curation failed")

    async def run(self, user_id: int, user_name: str, exchanges: list[str]) -> int:
        """-> 실행한 도구 호출 수"""
        owner = user_id == config.OWNER_ID
        system = PROMPT.format(
            who="the owner" if owner else "a guest",
            name=user_name,
            uid=user_id,
            main_rule=(
                "General context useful in every conversation (also shown to guests) goes to remember_main; "
                "the owner's private details go to remember_user."
                if owner else "This is a guest: only use remember_user / update_memory / forget_memory on their own notes."
            ),
            memories=await memory.context(user_id, user_name, owner),
            log="\n\n".join(exchanges),
        )
        tools = [t for t in aitools.specs(owner) if t.get("name") in TOOLS]
        items = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Update memory now."}]}]
        http: aiohttp.ClientSession = self.http()
        n = 0
        for _ in range(4):
            calls = []
            async for kind, data in self.gpt.events(http, self.model(), system, items, "low", tools):
                if kind == "item":
                    items.append(data)
                    if data.get("type") == "function_call":
                        calls.append(data)
            if not calls:
                break
            for c in calls:
                out = await aitools.run(http, c["name"], c.get("arguments"), user_id, owner, user_name)
                items.append({"type": "function_call_output", "call_id": c["call_id"], "output": out})
                n += 1
        if n:
            log.info("memory curation: %d changes for user %s", n, user_id)
        return n
