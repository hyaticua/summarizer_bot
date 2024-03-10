import os

import discord
from summarizer_bot.message import Message
from summarizer_bot.summarizer import Summarizer


message_limit = 10


discord_api_key = os.environ.get("DISCORD_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")

bot = discord.Bot()
summarizer = Summarizer(openai_api_key)


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")


@bot.slash_command()
async def summarize(ctx: discord.ApplicationContext):
    chan = bot.get_channel(ctx.channel_id)
    messages = [Message.convert(msg) for msg in 
                await chan.history(limit=message_limit).flatten()]
    summary = summarizer.summarize(messages)
    await ctx.respond(summary)


bot.run(discord_api_key)
