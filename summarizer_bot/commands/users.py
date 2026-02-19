import discord
from discord.ext import commands
from loguru import logger
from bot import ChatBot


class UserProfileMixin(commands.Cog):
    def __init__(self, bot: ChatBot):
        self.bot = bot

    @commands.slash_command()
    async def register_user(self, ctx: discord.ApplicationContext, info: str):
        logger.info("/register_user by {} (info_len={})", ctx.author.display_name, len(info))
        await ctx.defer()

        user_config = self.bot.config.get_server_config(ctx.author.id)

        info = info.strip()

        if len(info) > 128:
            await ctx.followup.send("Info too big")
            return
        
        if "\n" in info:
            await ctx.followup.send("newlines not allowed")
            return

        user_config["info"] = info

        await self.bot.config.set_server_config(ctx.author.id, user_config)
        await ctx.followup.send("User configuration updated <3")

