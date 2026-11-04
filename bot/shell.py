"""서버 명령 실행 (주인 전용). 읽기 전용으로 보이는 명령은 바로, 나머지는 확인 버튼 후 실행."""
import asyncio
import os
import re
import shlex

# off: 끔 / local: 봇 프로세스 안에서 / host: 도커 안에서 호스트로 (pid: host + privileged 필요, compose.shell.yaml)
MODE = os.environ.get("AI_SHELL", "off")
TIMEOUT = int(os.environ.get("AI_SHELL_TIMEOUT", 60))
MAX_OUT = 8000

SAFE = {
    "ls", "cat", "head", "tail", "grep", "egrep", "wc", "df", "du", "free", "uptime", "uname", "whoami", "id",
    "date", "hostname", "ps", "top", "pgrep", "ss", "ip", "lsblk", "stat", "find", "which",
    "journalctl", "dmesg", "sort", "uniq", "cut", "awk", "nproc", "lscpu", "ping", "dig", "nslookup", "curl",
    "echo", "pwd", "tree", "file", "sensors", "vmstat", "iostat",
}
SAFE_SUB = {
    "docker": {"ps", "logs", "stats", "images", "inspect", "info", "version", "top", "port"},
    "systemctl": {"status", "list-units", "list-timers", "is-active", "is-enabled", "show", "cat"},
    "git": {"status", "log", "diff", "show", "branch", "remote"},
    "apt": {"list", "show", "search", "policy"},
}
# 쓰기/치환/명령 연결이 들어가면 무조건 확인
UNSAFE_CHARS = re.compile(r"[;&`]|\$\(|>|<\(|\|\|")


def needs_confirm(cmd: str) -> bool:
    if UNSAFE_CHARS.search(cmd):
        return True
    for seg in cmd.split("|"):
        try:
            words = shlex.split(seg)
        except ValueError:
            return True
        if not words:
            return True
        w = words[0]
        if w == "sudo" or "=" in w:
            return True
        if w in SAFE_SUB:
            if len(words) < 2 or words[1] not in SAFE_SUB[w]:
                return True
        elif w not in SAFE:
            return True
        if w == "find" and any(x in words for x in ("-delete", "-exec", "-execdir", "-ok")):
            return True
        if w == "curl" and any(x.startswith(("-X", "-d", "--data", "-o", "-O", "-T", "--upload")) for x in words):
            return True
    return False


async def run(cmd: str) -> dict:
    if MODE == "host":
        argv = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--", "/bin/sh", "-c", cmd]
    else:
        argv = ["/bin/sh", "-c", cmd]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": f"timed out after {TIMEOUT}s"}
    text = out.decode(errors="replace")
    if len(text) > MAX_OUT:
        text = text[: MAX_OUT // 2] + "\n…(truncated)…\n" + text[-MAX_OUT // 2 :]
    return {"exit_code": proc.returncode, "output": text}
