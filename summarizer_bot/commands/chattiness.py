import discord
from discord.ext import commands
from bot import ChatBot


class ChattinessMixin(commands.Cog):
    def __init__(self, bot: ChatBot):
        self.bot = bot

    @commands.slash_command()
    async def chattiness_toggle(self, ctx: discord.ApplicationContext, enabled: bool):
        """Enable or disable automatic chat participation."""
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        await ctx.defer()

        server_config = self.bot.config.get_server_config(ctx.guild_id)

        if "chattiness" not in server_config:
            server_config["chattiness"] = {}

        server_config["chattiness"]["enabled"] = enabled

        await self.bot.config.set_server_config(ctx.guild_id, server_config)

        status = "enabled" if enabled else "disabled"
        await ctx.followup.send(f"Automatic chat participation **{status}**")

    @commands.slash_command()
    async def chattiness_cooldown(self, ctx: discord.ApplicationContext, seconds: int):
        """Set the minimum cooldown between auto-responses (in seconds)."""
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        await ctx.defer()

        if seconds < 0:
            await ctx.followup.send("Cooldown must be a positive number!")
            return

        server_config = self.bot.config.get_server_config(ctx.guild_id)

        if "chattiness" not in server_config:
            server_config["chattiness"] = {}

        server_config["chattiness"]["cooldown_seconds"] = seconds

        await self.bot.config.set_server_config(ctx.guild_id, server_config)

        await ctx.followup.send(f"Auto-response cooldown set to **{seconds} seconds**")

    @commands.slash_command()
    async def chattiness_min_messages(self, ctx: discord.ApplicationContext, count: int):
        """Set minimum messages required since bot last spoke before it can auto-respond."""
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        await ctx.defer()

        if count < 1:
            await ctx.followup.send("Minimum messages must be at least 1!")
            return

        server_config = self.bot.config.get_server_config(ctx.guild_id)

        if "chattiness" not in server_config:
            server_config["chattiness"] = {}

        server_config["chattiness"]["min_messages_since_last_response"] = count

        await self.bot.config.set_server_config(ctx.guild_id, server_config)

        await ctx.followup.send(f"Minimum messages since last response set to **{count}**")

    @commands.slash_command()
    async def chattiness_settings(self, ctx: discord.ApplicationContext):
        """View current chattiness settings for this server."""
        if ctx.author.name != self.bot.root_user:
            await ctx.send_response(
                content="Sorry, you don't have permission to use this command!",
                ephemeral=True)
            return

        await ctx.defer()

        config = self.bot.config.get_chattiness_config(ctx.guild_id)

        settings_text = f"""**Chattiness Settings:**
Enabled: **{config['enabled']}**
Cooldown: **{config['cooldown_seconds']} seconds**
Min message length: **{config['min_message_length']} characters**
Min messages since last response: **{config['min_messages_since_last_response']}**
Require multiple participants: **{config['require_multiple_messages']}**"""

        await ctx.followup.send(settings_text)
