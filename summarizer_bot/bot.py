import os

import discord
from message import Message
from summarizer import Summarizer

import datetime


MESSAGE_LIMIT = 200
DISCORD_MESSAGE_LINK_PREFIX = "https://discord.com/channels/"


discord_api_key = os.environ.get("DISCORD_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")

print(f"discord_api_key {discord_api_key}")
print(f"openai_api_key {openai_api_key}")


bot = discord.Bot()
summarizer = Summarizer(openai_api_key)


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")


def parse_message_from_link(message_link: str):
    if not message_link.startswith(DISCORD_MESSAGE_LINK_PREFIX):
        return None

    server_id, channel_id, msg_id = [
        int(x)
        for x in message_link.removeprefix(DISCORD_MESSAGE_LINK_PREFIX).split("/")
    ]

    guild = bot.get_guild(server_id)
    channel = guild.get_channel_or_thread(channel_id)
    return channel.get_partial_message(msg_id)


@bot.slash_command()
async def summarize(
    ctx: discord.ApplicationContext,
    minutes_ago: int = 30,
    start_message_link: str = None,
):
    chan = bot.get_channel(ctx.channel_id)

    start_msg = None
    start_time = datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)

    if start_message_link:
        start_msg = parse_message_from_link(start_message_link)
        if not start_msg:
            await ctx.respond("Unable to parse start message")
            return

    if not chan:
        await ctx.respond("Sorry I don't have access to read this channel.")
        return

    raw_messages = await chan.history(
        limit=MESSAGE_LIMIT, after=start_msg or start_time
    ).flatten()
    raw_messages.reverse()

    messages = []

    for msg in raw_messages:
        # skip bots and empty messages
        if not msg.content or msg.author.bot:
            continue

        author = msg.author
        if not isinstance(author, discord.Member):
            members = await msg.guild.query_members(user_ids=[msg.author.id])
            member = members[0]
            if member:
                author = member

        messages.append(Message.convert(msg, author))

    print(f"summarize request: {len(messages)=} {len(raw_messages)=} {len(messages)=}")

    summary = await summarizer.summarize(messages)
    await ctx.respond(summary)


bot.run(discord_api_key)
