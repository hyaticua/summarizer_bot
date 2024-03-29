import os

import discord
from message import Message
from summarizer import Summarizer

from loguru import logger


message_limit = 50


discord_api_key = os.environ.get("DISCORD_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")

print(f"discord_api_key {discord_api_key}")
print(f"openai_api_key {openai_api_key}")


bot = discord.Bot()
summarizer = Summarizer(openai_api_key)


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")


@bot.slash_command()
async def summarize(ctx: discord.ApplicationContext, num_messages: int = 10):
    num_messages = min(num_messages, message_limit)

    chan = bot.get_channel(ctx.channel_id)
    if not chan:
        await ctx.respond("Sorry I don't have access to read this channel.")
        return

    raw_messages = await chan.history(limit=num_messages).flatten()
    raw_messages.reverse()

    messages = []


    for msg in raw_messages:
        # skip bots and empty messages
        if not msg.content or msg.author.bot:
            continue

        author = msg.author
        # if not isinstance(author, discord.Member):
        #     members = await msg.guild.query_members(user_ids=[msg.author.id])
        #     author = members[0]

        messages.append(Message.convert(msg, author))

    print(f"summarize request: {num_messages=} {len(raw_messages)=} {len(messages)=}")

    summary = await summarizer.summarize(messages)
    await ctx.respond(summary)


bot.run(discord_api_key)
