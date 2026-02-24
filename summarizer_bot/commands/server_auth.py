import discord
from discord.ext import commands
from loguru import logger
from bot import ChatBot

VALID_MODES = ["ignore", "polite", "leave", "bad_bot"]


class ServerAuthMixin(commands.Cog):
    def __init__(self, bot: ChatBot):
        self.bot = bot

    @commands.slash_command(description="Authorize a server to use the bot")
    async def server_authorize(self, ctx: discord.ApplicationContext, server_id: str = None):
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        if server_id is None:
            if ctx.guild is None:
                await ctx.send_response("Must provide a server ID when used in DMs.", ephemeral=True)
                return
            guild_id = ctx.guild_id
        else:
            try:
                guild_id = int(server_id)
            except ValueError:
                await ctx.send_response("Invalid server ID.", ephemeral=True)
                return

        logger.info("/server_authorize {} by {}", guild_id, ctx.author.display_name)
        await ctx.defer(ephemeral=True)

        servers = self.bot.config.get_authorized_servers()
        first_use = servers is None
        servers = servers or []

        if guild_id in servers:
            await ctx.followup.send(f"Server `{guild_id}` is already authorized.")
            return

        servers.append(guild_id)
        await self.bot.config.set_authorized_servers(servers)

        # Remove from polite_declined if it was there
        declined = self.bot.config.get_polite_declined()
        if guild_id in declined:
            declined.remove(guild_id)
            self.bot.config.global_config["polite_declined"] = declined
            await self.bot.config._save()

        msg = f"Server `{guild_id}` authorized."
        if first_use:
            msg += "\n\n**Warning:** Server authorization is now active. Only listed servers will be served. Use `/server_authorize` to add more."
        await ctx.followup.send(msg)

    @commands.slash_command(description="Deauthorize a server from using the bot")
    async def server_deauthorize(self, ctx: discord.ApplicationContext, server_id: str = None):
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        if server_id is None:
            if ctx.guild is None:
                await ctx.send_response("Must provide a server ID when used in DMs.", ephemeral=True)
                return
            guild_id = ctx.guild_id
        else:
            try:
                guild_id = int(server_id)
            except ValueError:
                await ctx.send_response("Invalid server ID.", ephemeral=True)
                return

        logger.info("/server_deauthorize {} by {}", guild_id, ctx.author.display_name)
        await ctx.defer(ephemeral=True)

        servers = self.bot.config.get_authorized_servers()
        if servers is None or guild_id not in servers:
            await ctx.followup.send(f"Server `{guild_id}` is not in the authorized list.")
            return

        servers.remove(guild_id)
        await self.bot.config.set_authorized_servers(servers)

        await ctx.followup.send(f"Server `{guild_id}` deauthorized.")

    @commands.slash_command(description="List authorized servers and current unauthorized mode")
    async def server_auth_list(self, ctx: discord.ApplicationContext):
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        logger.info("/server_auth_list by {}", ctx.author.display_name)
        await ctx.defer(ephemeral=True)

        servers = self.bot.config.get_authorized_servers()
        mode = self.bot.config.get_unauthorized_mode()

        if servers is None:
            await ctx.followup.send(
                f"Server authorization is **not active** (all servers allowed).\n"
                f"Unauthorized mode: `{mode}`")
            return

        if not servers:
            server_list = "(none)"
        else:
            lines = []
            for sid in servers:
                guild = self.bot.get_guild(sid)
                name = guild.name if guild else "unknown"
                lines.append(f"- `{sid}` ({name})")
            server_list = "\n".join(lines)

        await ctx.followup.send(
            f"**Authorized servers:**\n{server_list}\n\n"
            f"**Unauthorized mode:** `{mode}`")

    @commands.slash_command(description="Set behavior mode for unauthorized servers")
    async def server_auth_mode(
        self,
        ctx: discord.ApplicationContext,
        mode: discord.Option(str, choices=VALID_MODES, description="How to handle unauthorized servers"),
    ):
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        logger.info("/server_auth_mode {} by {}", mode, ctx.author.display_name)
        await ctx.defer(ephemeral=True)

        old_mode = self.bot.config.get_unauthorized_mode()
        await self.bot.config.set_unauthorized_mode(mode)

        # Clear polite_declined when leaving polite mode
        if old_mode == "polite" and mode != "polite":
            await self.bot.config.clear_polite_declined()

        await ctx.followup.send(f"Unauthorized mode set to `{mode}`.")
