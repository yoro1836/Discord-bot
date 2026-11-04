import os
import shutil


def memory() -> tuple[int, int]:
    """(사용 가능, 전체) 바이트. /proc/meminfo 기준."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) * 1024
    return info.get("MemAvailable", info.get("MemFree", 0)), info["MemTotal"]


def disk(path: str = "/") -> tuple[int, int]:
    """(남은, 전체) 바이트"""
    u = shutil.disk_usage(path)
    return u.free, u.total


def load() -> tuple[float, float, float]:
    return os.getloadavg()


def gib(n: int) -> str:
    return f"{n / 1024**3:.1f}G"


def summary() -> str:
    ma, mt = memory()
    df, dt = disk()
    l1, l5, l15 = load()
    return (
        f"메모리 {gib(mt - ma)}/{gib(mt)} ({(mt - ma) / mt:.0%})\n"
        f"디스크 {gib(dt - df)}/{gib(dt)} ({(dt - df) / dt:.0%})\n"
        f"load {l1:.2f} {l5:.2f} {l15:.2f} (CPU {os.cpu_count()}개)"
    )
