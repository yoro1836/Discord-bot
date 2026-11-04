"""서버 자원이 임계값을 넘으면 DM으로 알림 (회복되면 한 번 더)."""
import os

from discord.ext import commands, tasks

from .. import config, sysinfo

MEM_PCT = float(os.environ.get("ALERT_MEM_PCT", 90))
DISK_PCT = float(os.environ.get("ALERT_DISK_PCT", 90))
LOAD_PER_CPU = float(os.environ.get("ALERT_LOAD_PER_CPU", 2.0))


class Monitor(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.alerting: set[str] = set()

    async def cog_load(self) -> None:
        self.check.start()

    async def cog_unload(self) -> None:
        self.check.cancel()

    def problems(self) -> dict[str, str]:
        out = {}
        ma, mt = sysinfo.memory()
        if (mt - ma) / mt * 100 >= MEM_PCT:
            out["mem"] = f"메모리 {(mt - ma) / mt:.0%} 사용 중 (남은 {sysinfo.gib(ma)})"
        df, dt = sysinfo.disk()
        if (dt - df) / dt * 100 >= DISK_PCT:
            out["disk"] = f"디스크 {(dt - df) / dt:.0%} 사용 중 (남은 {sysinfo.gib(df)})"
        l5 = sysinfo.load()[1]
        if l5 >= LOAD_PER_CPU * (os.cpu_count() or 1):
            out["load"] = f"5분 load {l5:.2f}"
        return out

    @tasks.loop(minutes=1)
    async def check(self) -> None:
        now = self.problems()
        new = {k: v for k, v in now.items() if k not in self.alerting}
        fixed = self.alerting - now.keys()
        self.alerting = set(now)
        if not new and not fixed:
            return
        lines = [f"🚨 {v}" for v in new.values()] + [f"✅ {k} 정상으로 돌아옴" for k in fixed]
        owner = await self.bot.fetch_user(config.OWNER_ID)
        await owner.send("\n".join(lines))

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Monitor(bot))
