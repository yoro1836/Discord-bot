import base64
import os
import time
from datetime import datetime

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from .. import aitools, config, curator, memory
from ..chatgpt import AuthError, ChatGPT

DB = config.DATA_DIR / "bot.db"
MAX_TURNS = 20  # 채널별로 기억하는 최근 메시지 수
MAX_TOOL_ROUNDS = 6
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

# 다른 사람도 답변 버튼으로 질문 가능 (내 ChatGPT 사용량을 쓰므로 횟수 제한)
GUEST_BUTTONS = os.environ.get("AI_GUEST_BUTTONS", "1") == "1"
GUEST_LIMIT, GUEST_WINDOW = (int(x) for x in os.environ.get("AI_GUEST_LIMIT", "5/600").split("/"))
_guest_uses: dict[int, list[float]] = {}


async def system_prompt(user_id: int, user_name: str, owner: bool) -> str:
    now = datetime.now(aitools.TZ).strftime("%Y-%m-%d %H:%M (%A) KST")
    who = "the bot's owner" if owner else "a guest (not the owner; reminders, server and main-memory tools are unavailable)"
    return (
        "You are an AI assistant living in Discord, owned by one person.\n"
        f"# Persona\n{await memory.get_persona()}\n\n"
        f"Current time: {now}. You are talking with {who}. Messages from guests are prefixed with [name]. "
        "Reply in the user's language, concisely, using Discord markdown. "
        "Use tools when they help: web search for anything recent or factual, fetch_url for links, "
        "get_weather, convert_currency, reminders. Cite sources briefly when you searched.\n"
        "You have long-term memory below and you maintain it yourself, without being asked. "
        "Whenever you learn something lasting (preferences, background, relationships, ongoing plans, "
        "how they like answers), save it with remember_user"
        + (" (general context useful in every chat: remember_main)" if owner else "")
        + ". If it changes an existing note, update_memory that [id] instead of adding a duplicate; "
        "merge duplicates and forget_memory notes that turn out wrong or obsolete. "
        "Do this alongside your answer, not instead of it. "
        "Don't announce routine saves. Never save secrets like passwords or tokens. "
        "Guests never see the owner's personal memories; don't reveal private details about the owner to them.\n\n"
        + await memory.context(user_id, user_name, owner)
    )


def chunks(text: str, n: int = 1990) -> list[str]:
    return [text[i : i + n] for i in range(0, len(text), n)] or ["(빈 응답)"]


async def to_data_urls(attachments) -> list[str]:
    out = []
    for a in attachments:
        ctype = (a.content_type or "").split(";")[0]
        if ctype in IMAGE_TYPES and a.size < 15 * 1024 * 1024:
            out.append(f"data:{ctype};base64,{base64.b64encode(await a.read()).decode()}")
    return out


def guest_allowed(user_id: int) -> bool:
    now = time.time()
    uses = [t for t in _guest_uses.get(user_id, []) if now - t < GUEST_WINDOW]
    ok = len(uses) < GUEST_LIMIT
    _guest_uses[user_id] = uses + [now] if ok else uses
    return ok


class AI(commands.Cog):
    ai = app_commands.Group(name="ai", description="AI 설정")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.gpt = ChatGPT(config.DATA_DIR / "chatgpt-auth.json")
        self.model = os.environ.get("AI_MODEL", "gpt-6.1-sol")
        self.effort = os.environ.get("AI_EFFORT", "low")
        self.http: aiohttp.ClientSession | None = None
        self.curator = curator.Curator(self.gpt, lambda: self.http, lambda: self.model)

    async def cog_load(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300))
        await memory.init()
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS ai_history (channel_id INTEGER, ts REAL, role TEXT, content TEXT)"
            )
            await db.commit()

    async def cog_unload(self) -> None:
        if self.http:
            await self.http.close()

    # --- 대화 기록 (채널별) ---
    async def history(self, channel_id: int) -> list[dict]:
        async with aiosqlite.connect(DB) as db:
            rows = await (await db.execute(
                "SELECT role, content FROM ai_history WHERE channel_id = ? ORDER BY ts DESC LIMIT ?",
                (channel_id, MAX_TURNS),
            )).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    async def remember(self, channel_id: int, prompt: str, reply: str) -> None:
        async with aiosqlite.connect(DB) as db:
            now = time.time()
            await db.executemany(
                "INSERT INTO ai_history VALUES (?, ?, ?, ?)",
                [(channel_id, now, "user", prompt), (channel_id, now + 0.001, "assistant", reply)],
            )
            await db.commit()

    async def forget(self, channel_id: int) -> None:
        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM ai_history WHERE channel_id = ?", (channel_id,))
            await db.commit()

    # --- 응답 생성: 도구 호출을 반복하며, 스트리밍으로 1초마다 메시지 수정 ---
    async def answer(self, send, edit, messages: list[dict], user_id: int, user_name: str, head: str = ""):
        """-> (답변 텍스트, 마지막 메시지). head: 첫 메시지 위에 붙일 줄"""
        owner = user_id == config.OWNER_ID
        items = ChatGPT.to_items(messages)
        tools = aitools.specs(owner)
        system = await system_prompt(user_id, user_name, owner)
        text, status, msg, last = "", "", None, 0.0

        async def show(final: bool = False) -> None:
            nonlocal msg, last
            body = head + (text or status or "…")
            if not final:
                if time.monotonic() - last < 1.0 or len(body) > 1980:
                    return
                body += " ▌"
            last = time.monotonic()
            if msg is None:
                msg = await send(body[:2000])
            else:
                await edit(msg, body[:2000])

        for _ in range(MAX_TOOL_ROUNDS):
            calls = []
            async for kind, data in self.gpt.events(
                self.http, self.model, system, items, self.effort, tools
            ):
                if kind == "text":
                    text += data
                    await show()
                    continue
                items.append(data)
                if data.get("type") == "function_call":
                    calls.append(data)
                elif data.get("type") == "web_search_call" and not text:
                    status = "🔎 검색 중"
                    await show()
            if not calls:
                break
            for c in calls:
                if not text:
                    status = aitools.LABELS.get(c["name"], "🔧 작업 중")
                    await show()
                out = await aitools.run(self.http, c["name"], c.get("arguments"), user_id, owner, user_name)
                items.append({"type": "function_call_output", "call_id": c["call_id"], "output": out})

        parts = chunks(head + text)
        if msg is None:
            msg = await send(parts[0])
        else:
            await edit(msg, parts[0])
        for p in parts[1:]:
            msg = await send(p)
        return text, msg

    async def run_chat(self, interaction: discord.Interaction, prompt: str, images=(), head: str = "") -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
        cid = interaction.channel_id or interaction.user.id
        try:
            urls = await to_data_urls(images)
        except discord.HTTPException as e:
            await interaction.edit_original_response(content=f"⚠️ 이미지 읽기 실패: {e}"[:1990])
            return
        msgs = await self.history(cid) + [{"role": "user", "content": prompt, "images": urls}]
        first = True

        async def send(t: str):
            nonlocal first
            if first:
                first = False
                return await interaction.edit_original_response(content=t)
            return await interaction.followup.send(t, wait=True)

        try:
            reply, last = await self.answer(
                send, lambda m, t: m.edit(content=t), msgs, interaction.user.id, interaction.user.display_name, head
            )
        except (AuthError, RuntimeError, aiohttp.ClientError) as e:
            await interaction.edit_original_response(content=f"⚠️ {e}"[:1990])
            return
        await self.remember(cid, prompt, reply)
        self.curator.note(interaction.user.id, interaction.user.display_name, prompt, reply)
        # 답변 밑 버튼: 명령어 다시 칠 필요 없이 이어서 질문
        await last.edit(view=ChatButtons())

    # --- 커맨드 ---
    @app_commands.command(name="c", description="AI에게 말 걸기 (비워두면 입력창이 뜸)")
    @app_commands.describe(prompt="질문 (비우면 여러 줄 입력창)", image="같이 보낼 이미지")
    async def c(
        self, interaction: discord.Interaction, prompt: str | None = None, image: discord.Attachment | None = None
    ) -> None:
        if prompt or image:
            await self.run_chat(interaction, prompt or "이 이미지 봐줘", images=[image] if image else ())
        else:
            await interaction.response.send_modal(AskModal())

    @ai.command(name="login", description="ChatGPT 계정 연결 (디바이스 코드)")
    async def login(self, interaction: discord.Interaction) -> None:
        try:
            dc = await self.gpt.start_device_login(self.http)
        except (AuthError, aiohttp.ClientError) as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"1. {dc['url']} 열고 ChatGPT로 로그인\n2. 코드 입력: `{dc['user_code']}` (15분 유효)", ephemeral=True
        )
        try:
            await self.gpt.finish_device_login(self.http, dc)
            msg = f"✅ 로그인 완료 ({self.gpt.tokens.get('plan') or '플랜 알 수 없음'})"
        except (AuthError, aiohttp.ClientError) as e:
            msg = f"⚠️ {e}"
        await interaction.followup.send(msg, ephemeral=True)

    @ai.command(name="logout", description="저장된 ChatGPT 토큰 삭제")
    async def logout(self, interaction: discord.Interaction) -> None:
        self.gpt.logout()
        await interaction.response.send_message("로그아웃했어요.", ephemeral=True)

    @ai.command(name="reset", description="이 채널의 대화 기록 지우기")
    async def reset(self, interaction: discord.Interaction) -> None:
        await self.forget(interaction.channel_id)
        await interaction.response.send_message("대화 기록을 지웠어요.", ephemeral=True)

    @ai.command(name="model", description="모델/추론 강도 확인·변경")
    @app_commands.describe(name="예: gpt-6.1-sol, gpt-6-astra, gpt-5.5", effort="low / medium / high")
    async def model_cmd(self, interaction: discord.Interaction, name: str | None = None, effort: str | None = None) -> None:
        self.model = name or self.model
        self.effort = effort or self.effort
        status = "로그인됨" if self.gpt.logged_in else "로그인 안 됨"
        await interaction.response.send_message(f"모델 `{self.model}`, 추론 `{self.effort}` ({status})", ephemeral=True)

    @ai.command(name="persona", description="AI 성격/말투 보기·바꾸기")
    async def persona(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(PersonaModal(await memory.get_persona()))

    @ai.command(name="memory", description="AI 기억 보기 (나만 보임)")
    @app_commands.describe(user="이 사람에 대한 기억만 보기")
    async def memory_cmd(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        if user:
            rows = await memory.notes(memory.user_scope(user.id))
            body = f"**{user.display_name}**\n" + ("\n".join(f"`{i}` {c}" for i, _, c in rows) or "없음")
        else:
            main = await memory.notes("main")
            people = await memory.users()
            body = "**메인 기억**\n" + ("\n".join(f"`{i}` {c}" for i, _, c in main) or "없음")
            body += "\n\n**사람별 기억**\n" + (
                "\n".join(f"{n or sc} ({cnt}개) <@{sc[5:]}>" for sc, n, cnt in people) or "없음"
            )
            body += "\n\n지우기: `/ai forget id` 또는 대화로 \"그거 잊어\""
        await interaction.response.send_message(body[:2000], ephemeral=True)

    @ai.command(name="tidy", description="지금 바로 기억 정리 (중복 합치기, 오래된 것 정리)")
    @app_commands.describe(user="이 사람 기억 정리 (기본: 나)")
    async def tidy(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        u = user or interaction.user
        try:
            n = await self.curator.run(u.id, u.display_name, ["(No new conversation. Just tidy: merge duplicates, fix contradictions.)"])
        except (AuthError, RuntimeError, aiohttp.ClientError) as e:
            await interaction.followup.send(f"⚠️ {e}"[:1990], ephemeral=True)
            return
        await interaction.followup.send(f"정리 끝 ({n}건 변경). `/ai memory`로 확인", ephemeral=True)

    @ai.command(name="forget", description="기억 지우기 (id, 또는 사람 지정 시 그 사람 기억 전부)")
    async def forget_cmd(
        self, interaction: discord.Interaction, id: int | None = None, user: discord.User | None = None
    ) -> None:
        if user:
            await memory.clear(memory.user_scope(user.id))
            msg = f"{user.display_name}에 대한 기억을 모두 지웠어요."
        elif id is not None:
            msg = "지웠어요." if await memory.delete(id) else "그런 기억이 없어요."
        else:
            msg = "id나 사람을 지정해 주세요."
        await interaction.response.send_message(msg, ephemeral=True)

    # --- 봇 DM: 그냥 말 걸면 답장 ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.id != config.OWNER_ID or message.guild is not None:
            return
        if (not message.content and not message.attachments) or message.content.startswith("/"):
            return
        # 전달(Forward)된 메시지도 내용을 읽음
        snaps = getattr(message, "message_snapshots", None) or []
        content = message.content or "\n".join(f"(전달된 메시지) {s.content}" for s in snaps) or "이 이미지 봐줘"
        atts = list(message.attachments) + [a for s in snaps for a in s.attachments]
        cid = message.channel.id
        msgs = await self.history(cid) + [{"role": "user", "content": content, "images": await to_data_urls(atts)}]
        try:
            async with message.channel.typing():
                reply, _ = await self.answer(
                    message.channel.send, lambda m, t: m.edit(content=t), msgs,
                    message.author.id, message.author.display_name,
                )
        except (AuthError, RuntimeError, aiohttp.ClientError) as e:
            await message.channel.send(f"⚠️ {e}"[:1990])
            return
        await self.remember(cid, content, reply)
        self.curator.note(message.author.id, message.author.display_name, content, reply)


class PersonaModal(discord.ui.Modal, title="AI 페르소나"):
    def __init__(self, current: str) -> None:
        super().__init__()
        self.text = discord.ui.TextInput(
            label="성격, 말투, 이름, 규칙 등", style=discord.TextStyle.paragraph, default=current, max_length=4000
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await memory.set_persona(self.text.value or memory.DEFAULT_PERSONA)
        await interaction.response.send_message("페르소나를 바꿨어요.", ephemeral=True)


class AskModal(discord.ui.Modal, title="AI에게 묻기"):
    prompt = discord.ui.TextInput(label="질문", style=discord.TextStyle.paragraph, max_length=4000)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog: AI = interaction.client.get_cog("AI")
        q = self.prompt.value
        name = interaction.user.display_name
        quoted = q if len(q) <= 300 else q[:300] + "…"
        # 입력창 내용은 남들에게 안 보이므로 답변 위에 질문을 같이 보여줌
        head = "> " + quoted.replace("\n", "\n> ") + f"\n> — {name}\n"
        text = q if interaction.user.id == config.OWNER_ID else f"[{name}] {q}"
        await cog.run_chat(interaction, text, head=head)


class ChatButtons(discord.ui.View):
    """재시작 후에도 동작하도록 timeout 없음 + 고정 custom_id"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == config.OWNER_ID:
            return True
        if interaction.data.get("custom_id") == "ai:cont" and GUEST_BUTTONS:
            if guest_allowed(interaction.user.id):
                return True
            await interaction.response.send_message("잠시 후에 다시 눌러주세요.", ephemeral=True)
            return False
        await interaction.response.send_message("이 버튼은 주인만 쓸 수 있어요.", ephemeral=True)
        return False

    @discord.ui.button(label="이어서 질문", emoji="💬", style=discord.ButtonStyle.primary, custom_id="ai:cont")
    async def cont(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AskModal())

    @discord.ui.button(label="새 대화", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="ai:new")
    async def new(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.client.get_cog("AI").forget(interaction.channel_id)
        await interaction.response.send_modal(AskModal())


@app_commands.context_menu(name="AI에게 묻기")
async def ask_about(interaction: discord.Interaction, message: discord.Message) -> None:
    cog: AI = interaction.client.get_cog("AI")
    prompt = (
        "다음 디스코드 메시지를 상황에 맞게 다뤄줘: 길면 요약, 외국어면 한국어 번역, 질문이면 답변, "
        "링크면 읽고 요약, 이미지가 있으면 글자 추출/번역하거나 설명.\n\n"
        f"{message.author.display_name}: {message.content or '(이미지만 있음)'}"
    )
    await cog.run_chat(interaction, prompt, images=message.attachments)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AI(bot))
    bot.tree.add_command(ask_about)
    bot.add_view(ChatButtons())
