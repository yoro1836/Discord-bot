"""ChatGPT 구독(OAuth)으로 Codex 백엔드를 호출. 흐름은 openai/codex CLI와 동일."""
import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
BASE_URL = "https://chatgpt.com/backend-api/codex"
HEADERS = {"originator": "codex_cli_rs", "User-Agent": "codex_cli_rs"}


class AuthError(Exception):
    pass


def _jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class ChatGPT:
    def __init__(self, auth_path: Path) -> None:
        self.auth_path = auth_path
        self._lock = asyncio.Lock()
        self.tokens: dict | None = None
        if auth_path.exists():
            self.tokens = json.loads(auth_path.read_text())

    @property
    def logged_in(self) -> bool:
        return self.tokens is not None

    def _save(self, tokens: dict) -> None:
        claims = _jwt_claims(tokens["id_token"]).get("https://api.openai.com/auth", {})
        tokens["account_id"] = claims.get("chatgpt_account_id")
        tokens["plan"] = claims.get("chatgpt_plan_type")
        self.tokens = tokens
        self.auth_path.write_text(json.dumps(tokens))
        self.auth_path.chmod(0o600)

    def logout(self) -> None:
        self.tokens = None
        self.auth_path.unlink(missing_ok=True)

    # --- 로그인 (디바이스 코드) ---
    async def start_device_login(self, s: aiohttp.ClientSession) -> dict:
        async with s.post(f"{ISSUER}/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID}) as r:
            if r.status != 200:
                raise AuthError(f"device code 요청 실패: {r.status}")
            d = await r.json(content_type=None)
        return {
            "url": f"{ISSUER}/codex/device",
            "user_code": d.get("user_code") or d["usercode"],
            "device_auth_id": d["device_auth_id"],
            "interval": int(str(d.get("interval", 5)).strip() or 5),
        }

    async def finish_device_login(self, s: aiohttp.ClientSession, dc: dict) -> None:
        deadline = time.time() + 15 * 60
        while True:
            async with s.post(
                f"{ISSUER}/api/accounts/deviceauth/token",
                json={"device_auth_id": dc["device_auth_id"], "user_code": dc["user_code"]},
            ) as r:
                if r.status == 200:
                    code = await r.json(content_type=None)
                    break
                if r.status not in (403, 404):
                    raise AuthError(f"로그인 실패: {r.status}")
            if time.time() > deadline:
                raise AuthError("15분 안에 로그인하지 않아 취소됐어요.")
            await asyncio.sleep(dc["interval"])
        form = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code["authorization_code"],
            "redirect_uri": f"{ISSUER}/deviceauth/callback",
            "code_verifier": code["code_verifier"],
        }
        async with s.post(f"{ISSUER}/oauth/token", data=form) as r:
            if r.status != 200:
                raise AuthError(f"토큰 교환 실패: {r.status} {await r.text()}")
            self._save(await r.json(content_type=None))

    # --- 토큰 갱신 ---
    async def _access_token(self, s: aiohttp.ClientSession, force: bool = False) -> str:
        async with self._lock:
            if not self.tokens:
                raise AuthError("로그인이 필요해요. `/ai login`")
            exp = _jwt_claims(self.tokens["access_token"]).get("exp", 0)
            if force or exp - time.time() < 300:
                body = {
                    "client_id": CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": self.tokens["refresh_token"],
                }
                async with s.post(f"{ISSUER}/oauth/token", json=body) as r:
                    if r.status != 200:
                        raise AuthError(f"토큰 갱신 실패({r.status}). `/ai login`으로 다시 로그인해 주세요.")
                    new = await r.json(content_type=None)
                self._save({**self.tokens, **{k: v for k, v in new.items() if v}})
            return self.tokens["access_token"]

    # --- 응답 스트리밍 ---
    @staticmethod
    def to_items(messages: list[dict]) -> list[dict]:
        """[{"role", "content", "images"?}] -> Responses API input items"""
        items = []
        for m in messages:
            kind = "output_text" if m["role"] == "assistant" else "input_text"
            parts = [{"type": kind, "text": m["content"]}]
            parts += [{"type": "input_image", "image_url": u} for u in m.get("images", [])]
            items.append({"type": "message", "role": m["role"], "content": parts})
        return items

    async def events(
        self,
        s: aiohttp.ClientSession,
        model: str,
        system: str,
        items: list[dict],
        effort: str = "low",
        tools: list[dict] = (),
    ) -> AsyncIterator[tuple[str, object]]:
        """-> ("text", 조각) | ("item", 완료된 output item: message/reasoning/function_call...)"""
        dev = {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": system}]}
        body = {
            "model": model,
            "stream": True,
            "input": [dev, *items],
            "tools": list(tools),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "reasoning": {"effort": effort},
            "store": False,
            # store=false라 도구 호출 뒤 이어갈 때 추론 내용을 암호화된 채로 돌려받아 다시 보냄
            "include": ["reasoning.encrypted_content"],
        }
        for attempt in range(2):
            token = await self._access_token(s, force=attempt > 0)
            headers = {
                **HEADERS,
                "Authorization": f"Bearer {token}",
                "chatgpt-account-id": self.tokens.get("account_id") or "",
                "Accept": "text/event-stream",
            }
            async with s.post(f"{BASE_URL}/responses", json=body, headers=headers) as r:
                if r.status == 401 and attempt == 0:
                    continue
                if r.status != 200:
                    raise RuntimeError(f"ChatGPT 오류 {r.status}: {(await r.text())[:500]}")
                buf = b""
                async for chunk in r.content.iter_any():  # 한 줄이 64KB를 넘을 수 있어서 직접 나눔
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        line = raw.decode().strip()
                        if not line.startswith("data:"):
                            continue
                        ev = json.loads(line[5:])
                        t = ev.get("type")
                        if t == "response.output_text.delta":
                            yield "text", ev["delta"]
                        elif t == "response.output_item.done":
                            item = {k: v for k, v in ev["item"].items() if k not in ("id", "status")}
                            yield "item", item
                        elif t == "response.failed":
                            err = ev.get("response", {}).get("error") or {}
                            raise RuntimeError(f"ChatGPT 오류: {err.get('message', ev)}")
                return
