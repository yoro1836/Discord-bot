"""ChatGPT가 호출하는 도구들 (function calling)."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

from . import memory, shell, sysinfo, todos, webtext
from .cogs import reminders

TZ = ZoneInfo("Asia/Seoul")
WEB_SEARCH = {"type": "web_search", "external_web_access": True}

WMO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림", 45: "안개", 48: "안개",
    51: "이슬비", 53: "이슬비", 55: "이슬비", 61: "비", 63: "비", 65: "강한 비", 66: "어는 비", 67: "어는 비",
    71: "눈", 73: "눈", 75: "많은 눈", 77: "싸락눈", 80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "눈 소나기", 86: "눈 소나기", 95: "뇌우", 96: "우박 뇌우", 99: "우박 뇌우",
}


def fn(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": False,
        "parameters": {"type": "object", "properties": props, "required": required or list(props)},
    }


PUBLIC = [
    fn("get_weather", "Current weather and 3-day forecast for a place (any language name).",
       {"location": {"type": "string"}}),
    fn("convert_currency", "Convert money using today's exchange rate.",
       {"amount": {"type": "number"}, "src": {"type": "string", "description": "ISO code, e.g. USD"},
        "dst": {"type": "string", "description": "ISO code, e.g. KRW"}}),
    fn("remember_user", "Save a lasting fact about the person you're talking to (preferences, background, how they like "
       "answers). Keep it short; one fact per call. Don't save trivia or one-off details.",
       {"note": {"type": "string"}}),
    fn("update_memory", "Rewrite an existing memory by its [id] (e.g. when a fact changed).",
       {"id": {"type": "integer"}, "note": {"type": "string"}}),
    fn("forget_memory", "Delete a memory by its [id] (when asked to forget, or it's wrong).",
       {"id": {"type": "integer"}}),
    fn("fetch_url", "Read a web page's text or a YouTube video's transcript. If it fails, use web search instead.",
       {"url": {"type": "string"}}),
]
OWNER_ONLY = [
    fn("remember_main", "Save a lasting fact to the shared main memory: general context useful in every "
       "conversation, including with guests (e.g. the owner's projects, server, group in-jokes). "
       "Put the owner's private details in remember_user instead.", {"note": {"type": "string"}}),
    fn("set_reminder", "Schedule a Discord DM reminder for the owner.",
       {"due": {"type": "string", "description": "ISO 8601 datetime with offset, e.g. 2026-11-04T18:00:00+09:00"},
        "text": {"type": "string"}}),
    fn("list_reminders", "List the owner's scheduled reminders.", {}),
    fn("delete_reminder", "Delete a reminder by id.", {"id": {"type": "integer"}}),
    fn("server_status", "Memory/disk/load of the server this bot runs on.", {}),
    fn("add_todo", "Add an item to the owner's to-do list.",
       {"text": {"type": "string"}, "due": {"type": "string", "description": "optional date/time, e.g. 2026-11-05 or 2026-11-05T18:00"}},
       ["text"]),
    fn("list_todos", "List the owner's to-do items (open ones unless include_done).",
       {"include_done": {"type": "boolean"}}, []),
    fn("update_todo", "Mark a to-do done/undone, or change its text or due.",
       {"id": {"type": "integer"}, "done": {"type": "boolean"}, "text": {"type": "string"}, "due": {"type": "string"}},
       ["id"]),
    fn("delete_todo", "Delete a to-do item.", {"id": {"type": "integer"}}),
]
SHELL_TOOL = fn(
    "run_command",
    "Run a shell command on the owner's Linux server (Ubuntu on Oracle Cloud; the bot runs there in Docker). "
    "Read-only commands run immediately; anything that changes state asks the owner to approve first. "
    "Prefer non-interactive flags (e.g. top -bn1, --no-pager). Output is truncated to ~8KB.",
    {"command": {"type": "string"}, "reason": {"type": "string", "description": "one short line shown to the owner"}},
)


def specs(owner: bool) -> list[dict]:
    if not owner:
        return [WEB_SEARCH, *PUBLIC]
    return [WEB_SEARCH, *PUBLIC, *OWNER_ONLY, *([SHELL_TOOL] if shell.MODE != "off" else [])]


LABELS = {
    "remember_user": "🧠 기억하는 중", "remember_main": "🧠 기억하는 중", "update_memory": "🧠 기억 고치는 중",
    "forget_memory": "🧠 잊는 중",
    "get_weather": "🌤️ 날씨 확인", "convert_currency": "💱 환율 확인", "fetch_url": "🔗 링크 읽는 중",
    "set_reminder": "⏰ 리마인더 설정", "list_reminders": "⏰ 리마인더 확인", "delete_reminder": "⏰ 리마인더 삭제",
    "server_status": "🖥️ 서버 상태 확인", "run_command": "💻 명령 실행 중",
    "add_todo": "📝 할 일 추가", "list_todos": "📝 할 일 확인", "update_todo": "📝 할 일 수정", "delete_todo": "📝 할 일 삭제",
}


async def _json(s: aiohttp.ClientSession, url: str, **kw) -> dict:
    async with s.get(url, timeout=aiohttp.ClientTimeout(total=20), **kw) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


async def get_weather(s, location: str) -> dict:
    # 한글 지명은 OSM Nominatim으로 찾기 (open-meteo 지오코딩은 한글 검색이 안 됨)
    geo = await _json(
        s, "https://nominatim.openstreetmap.org/search",
        params={"q": location, "format": "jsonv2", "limit": 1, "accept-language": "ko"},
        headers={"User-Agent": "personal-discord-bot/1.0"},
    )
    if not geo:
        return {"error": f"location not found: {location}"}
    g = geo[0]
    w = await _json(s, "https://api.open-meteo.com/v1/forecast", params={
        "latitude": g["lat"], "longitude": g["lon"], "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    })
    c, d = w["current"], w["daily"]
    return {
        "place": g["display_name"],
        "now": {**c, "weather": WMO.get(c["weather_code"])},
        "daily": [
            {"date": d["time"][i], "weather": WMO.get(d["weather_code"][i]), "min": d["temperature_2m_min"][i],
             "max": d["temperature_2m_max"][i], "rain_chance": d["precipitation_probability_max"][i]}
            for i in range(len(d["time"]))
        ],
    }


async def convert_currency(s, amount: float, src: str, dst: str) -> dict:
    src, dst = src.upper(), dst.upper()
    d = await _json(s, f"https://open.er-api.com/v6/latest/{src}")
    if d.get("result") != "success" or dst not in d.get("rates", {}):
        return {"error": "unknown currency code"}
    rate = d["rates"][dst]
    return {"amount": amount, "src": src, "dst": dst, "rate": rate, "result": amount * rate,
            "updated": d.get("time_last_update_utc")}


async def fetch_url(s, url: str) -> dict:
    vid = webtext.youtube_id(url)
    try:
        if vid:
            return {"youtube_transcript": await webtext.youtube_transcript(vid)}
        return {"text": await webtext.page_text(s, url)}
    except Exception as e:  # noqa: BLE001 - 모델에게 실패를 알려서 웹 검색으로 넘어가게
        return {"error": f"{type(e).__name__}: {e}"[:300]}


async def run(
    s: aiohttp.ClientSession, name: str, args_json: str, user_id: int, owner: bool, user_name: str = "",
    confirm=None,
) -> str:
    """confirm: async (command, reason) -> bool, 확인 버튼을 띄우는 함수"""
    # 주인은 모든 기억을, 손님은 자기 기억만 고치거나 지울 수 있음
    scopes = None if owner else [memory.user_scope(user_id)]
    try:
        a = json.loads(args_json or "{}")
        if name == "remember_user":
            out = {"id": await memory.add(memory.user_scope(user_id), a["note"], user_name)}
        elif name == "update_memory":
            out = {"updated": await memory.update(int(a["id"]), a["note"], scopes)}
        elif name == "forget_memory":
            out = {"deleted": await memory.delete(int(a["id"]), scopes)}
        elif name == "get_weather":
            out = await get_weather(s, a["location"])
        elif name == "convert_currency":
            out = await convert_currency(s, float(a["amount"]), a["src"], a["dst"])
        elif name == "fetch_url":
            out = await fetch_url(s, a["url"])
        elif not owner:
            out = {"error": "not allowed"}
        elif name == "remember_main":
            out = {"id": await memory.add("main", a["note"], user_name)}
        elif name == "set_reminder":
            due = datetime.fromisoformat(a["due"])
            if due.tzinfo is None:
                due = due.replace(tzinfo=TZ)
            ts = int(due.timestamp())
            if ts <= time.time():
                out = {"error": "due time is in the past"}
            else:
                out = {"id": await reminders.add(user_id, ts, a["text"]), "due": due.isoformat()}
        elif name == "list_reminders":
            out = [{"id": i, "due": datetime.fromtimestamp(d, TZ).isoformat(), "text": t}
                   for i, d, t in await reminders.list_for(user_id)]
        elif name == "delete_reminder":
            out = {"deleted": await reminders.delete(user_id, int(a["id"]))}
        elif name == "server_status":
            out = {"summary": sysinfo.summary()}
        elif name == "add_todo":
            out = {"id": await todos.add(user_id, a["text"], a.get("due"))}
        elif name == "list_todos":
            out = await todos.list_for(user_id, bool(a.get("include_done")))
        elif name == "update_todo":
            tid, ok = int(a["id"]), False
            if "done" in a:
                ok = await todos.set_done(user_id, tid, bool(a["done"]))
            if a.get("text") or a.get("due"):
                ok = await todos.edit(user_id, tid, a.get("text"), a.get("due")) or ok
            out = {"updated": ok}
        elif name == "delete_todo":
            out = {"deleted": await todos.delete(user_id, int(a["id"]))}
        elif name == "run_command" and shell.MODE != "off":
            cmd = a["command"]
            if shell.needs_confirm(cmd) and not (confirm and await confirm(cmd, a.get("reason", ""))):
                out = {"error": "the owner declined (or didn't answer); don't retry the same command"}
            else:
                out = await shell.run(cmd)
        else:
            out = {"error": f"unknown tool {name}"}
    except Exception as e:  # noqa: BLE001
        out = {"error": f"{type(e).__name__}: {e}"[:300]}
    return json.dumps(out, ensure_ascii=False)[:40000]
