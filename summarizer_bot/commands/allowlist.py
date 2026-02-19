import discord
from discord.ext import commands
from loguru import logger
from bot import ChatBot


class ChatAllowlistMixin(commands.Cog):
    def __init__(self, bot: ChatBot):
        self.bot = bot

    @commands.slash_command()
    async def chat_allowlist_add(self, ctx: discord.ApplicationContext, channel: discord.TextChannel):
        if ctx.author.name != self.bot.root_user:
            logger.warning("Unauthorized /chat_allowlist_add attempt by {}", ctx.author.display_name)
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        logger.info("/chat_allowlist_add #{} by {}", channel.name, ctx.author.display_name)
        await ctx.defer()
        
        server_config = self.bot.config.get_server_config(ctx.guild_id)

        if "chat_allowlist" not in server_config:
            server_config["chat_allowlist"] = []
        server_config["chat_allowlist"].append(channel.id)

        await self.bot.config.set_server_config(ctx.guild_id, server_config)

        await ctx.followup.send(f"Channel added to allowlist: **{channel.name}**")


    @commands.slash_command()
    async def chat_allowlist_remove(self, ctx: discord.ApplicationContext, channel: discord.TextChannel):
        if ctx.author.name != self.bot.root_user:
            logger.warning("Unauthorized /chat_allowlist_remove attempt by {}", ctx.author.display_name)
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        logger.info("/chat_allowlist_remove #{} by {}", channel.name, ctx.author.display_name)
        await ctx.defer()
        
        server_config = self.bot.config.get_server_config(ctx.guild_id)

        if "chat_allowlist" not in server_config:
            await ctx.followup.send("No chat allowlist found")
            return
        
        server_config["chat_allowlist"].remove(channel.id)

        await self.bot.config.set_server_config(ctx.guild_id, server_config)

        await ctx.followup.send(f"Channel removed from allowlist: **{channel.name}**")


    @commands.slash_command()
    async def chat_allowlist_list(self, ctx: discord.ApplicationContext):
        if ctx.author.name != self.bot.root_user:
            logger.warning("Unauthorized /chat_allowlist_list attempt by {}", ctx.author.display_name)
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        logger.info("/chat_allowlist_list by {}", ctx.author.display_name)
        await ctx.defer()
        
        server_config = self.bot.config.get_server_config(ctx.guild_id)

        if "chat_allowlist" not in server_config or not server_config["chat_allowlist"]:
            await ctx.followup.send("No chat allowlist found")
            return
        
        converter = commands.TextChannelConverter()

        async def get_chan_name(chan):
            return (await converter.convert(ctx, str(chan))).name 
        
        allowlist = [f"**{await get_chan_name(chan)}**" for chan in server_config["chat_allowlist"]]

        await ctx.followup.send("I am allowed to chat in the following channels:\n" + "\n".join(allowlist))


    @commands.slash_command()
    async def chat_allowlist_clear(self, ctx: discord.ApplicationContext):
        if ctx.author.name != self.bot.root_user:
            logger.warning("Unauthorized /chat_allowlist_clear attempt by {}", ctx.author.display_name)
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        logger.info("/chat_allowlist_clear by {}", ctx.author.display_name)
        await ctx.defer()
        
        server_config = self.bot.config.get_server_config(ctx.guild_id)

        if "chat_allowlist" in server_config:    
            server_config["chat_allowlist"] = []
            await self.bot.config.set_server_config(ctx.guild_id, server_config)

        await ctx.followup.send("Server chat allowlist cleared.")
