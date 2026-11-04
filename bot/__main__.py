import logging

import discord
from discord import app_commands
from discord.ext import commands

from . import config

COGS = ["bot.cogs.reminders", "bot.cogs.ai", "bot.cogs.monitor"]


class Bot(commands.Bot):
    def __init__(self) -> None:
        # 유저 설치 앱은 메시지 내용을 못 읽으므로 기본 intents면 충분
        # 모든 슬래시 커맨드: 유저 설치 + DM/서버/그룹DM 어디서나
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            allowed_installs=app_commands.AppInstallationType(guild=False, user=True),
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=True, private_channel=True
            ),
        )
        self.tree.interaction_check = self.owner_only

    async def owner_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == config.OWNER_ID:
            return True
        await interaction.response.send_message("개인용 봇이에요.", ephemeral=True)
        return False

    async def setup_hook(self) -> None:
        for cog in COGS:
            await self.load_extension(cog)
        synced = await self.tree.sync()
        logging.info("synced %d commands", len(synced))

    async def on_ready(self) -> None:
        logging.info("logged in as %s", self.user)


def main() -> None:
    discord.utils.setup_logging(level=logging.INFO)
    Bot().run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
