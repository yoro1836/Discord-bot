"""링크 본문 / 유튜브 자막 추출."""
import asyncio
import re
from html.parser import HTMLParser

import aiohttp

YT = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)([\w-]{11})")
MAX_CHARS = 40000


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        elif not self.depth and data.strip():
            self.parts.append(data.strip())


def youtube_id(url: str) -> str | None:
    m = YT.search(url)
    return m.group(1) if m else None


def _transcript(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    t = YouTubeTranscriptApi().fetch(video_id, languages=["ko", "en", "ja"])
    return " ".join(s.text for s in t)


async def youtube_transcript(video_id: str) -> str:
    return (await asyncio.to_thread(_transcript, video_id))[:MAX_CHARS]


async def page_text(s: aiohttp.ClientSession, url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
    async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        if "html" not in r.headers.get("Content-Type", "html"):
            return (await r.text(errors="replace"))[:MAX_CHARS]
        html = await r.text(errors="replace")
    p = _Text()
    p.feed(html)
    return (f"제목: {p.title}\n\n" + "\n".join(p.parts))[:MAX_CHARS]
