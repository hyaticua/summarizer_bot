import discord 
from discord.ext import commands
from utils import concat_messages
from bot import ChatBot


class SummarizeMixin(commands.Cog):
    def __init__(self, bot: ChatBot):
        self.bot = bot
        
    @commands.slash_command()
    async def summarize(self, ctx: discord.ApplicationContext, num_messages: int = 20, accent: str = None):
        await ctx.defer()

        try:
            raw_messages = await self.bot.fetch_messages(ctx.channel_id, num_messages)
            messages, involved_users = await self.bot.process_messages(raw_messages)
            user_profiles = self.bot.get_user_profiles(involved_users) 
            msg_str, user_profs_str = concat_messages(messages, user_profiles)

            server_config = self.bot.config.get_server_config(ctx.guild_id)
            profile = server_config.get("profile", "")

            if accent:
                profile += (f" Prioritize writing your summaries in way with an accent obviously from or in the manner of {accent}. "
                            "If the accent is something non-human, then instead summarize attempting to roleplay as that thing. ")

            if not msg_str:
                return "Sorry, there was nothing to summarize :)"
            
            prompt = (f"Additional instructions: {profile}\n\n"
                    f"User profiles: \n{user_profs_str}\n\n"
                    f"Chat log: \n{msg_str}\n\n"
                    "Summary: ")
            
            summary = await self.bot.llm_client.generate(prompt)
            await ctx.followup.send(summary)
        except discord.errors.Forbidden as e:
            await ctx.author.send("Sorry it looks like I don't have access!")
            await ctx.delete()
            # ctx.followup.send("Sorry it looks like I don't have access!")
