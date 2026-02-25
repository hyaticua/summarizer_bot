import discord
from discord.ext import commands
from loguru import logger
from bot import ChatBot
from memory import MemoryStore

VALID_MODES = ["off", "local"]


class MemoryMixin(commands.Cog):
    def __init__(self, bot: ChatBot):
        self.bot = bot

    @commands.slash_command(description="Turn bot memory on or off")
    async def memory(
        self,
        ctx: discord.ApplicationContext,
        mode: discord.Option(str, choices=VALID_MODES, description="'local' to enable, 'off' to disable"),
    ):
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        logger.info("/memory {} by {}", mode, ctx.author.display_name)
        await ctx.defer(ephemeral=True)

        old_mode = self.bot.config.get_memory_mode()
        await self.bot.config.set_memory_mode(mode)

        if mode == "local" and self.bot.memory_store is None:
            self.bot.memory_store = MemoryStore()
            logger.info("MemoryStore initialized via /memory command")
        elif mode == "off" and self.bot.memory_store is not None:
            self.bot.memory_store = None
            logger.info("MemoryStore disabled via /memory command")

        if old_mode == mode:
            await ctx.followup.send(f"Memory mode is already `{mode}`.")
        else:
            await ctx.followup.send(f"Memory mode changed from `{old_mode}` to `{mode}`.")
