import os

import discord
from message import Message
from summarizer import Summarizer

import json

import aiofiles

import pandas as pd


message_limit = 100
root_user = ".namielle"


discord_api_key = os.environ.get("DISCORD_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")

print(f"discord_api_key {discord_api_key}")
print(f"openai_api_key {openai_api_key}")


bot = discord.Bot()


server_id = 830082778795999312
message_data = []

try:
    with open("config.json", "r") as f:
        global_config = json.loads(f.read())
except:
    global_config = {}


async def record_metadata(
    channel: discord.TextChannel | discord.Thread, target_server: discord.Guild
):
    permissions = channel.permissions_for(target_server.me)
    location_type = "channel" if isinstance(channel, discord.TextChannel) else "thread"

    if permissions.read_message_history:
        print(f"Fetching messages for {location_type}: {channel.name}")

        async for message in channel.history(limit=None):
            message_data.append(
                {
                    "author_id": str(message.author.id),
                    "author_name": str(message.author.name),
                    "bot": message.author.bot,
                    "datetime": message.created_at,
                    "length": len(message.content),
                    "num_reactions": len(message.reactions),
                    "location": channel.name,
                    "location_type": location_type,
                }
            )
    else:
        print(f"Unable to fetch messages for {location_type}: {channel.name}")


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")

    target_server = None

    for guild in bot.guilds:
        if guild.id == server_id:
            target_server = guild
            break

    for channel in target_server.text_channels:
        record_metadata(
            channel,
            target_server,
            channel.name,
        )

    for thread in target_server.threads:
        print(f"Fetching messages for thread: {thread.name}")

        async for message in thread.history(limit=None):
            record_metadata(message, thread.name, "thread")

    df = pd.DataFrame(message_data)
    df.to_csv("message_stats.csv", index=False)

    print("Message data collected and saved!")


def get_config(id: int) -> dict:
    return global_config.get(str(id), {})


async def set_config(id: int, configuration: dict):
    global_config[str(id)] = configuration
    async with aiofiles.open("config.json", mode="w") as f:
        await f.write(json.dumps(global_config, indent=2))


@bot.slash_command()
async def config(
    ctx: discord.ApplicationContext, profile: str = None, model: str = None
):
    if ctx.author.name != root_user:
        await ctx.send_response(
            content="Sorry, you don't have permission to use this command!",
            ephemeral=True,
        )
        return

    await ctx.defer()

    config = get_config(ctx.guild_id)

    if profile:
        config["profile"] = profile
    if model:
        config["model"] = model

    await set_config(ctx.guild_id, config)

    await ctx.followup.send("Server config updated <3 <3")


@bot.slash_command()
async def summarize(
    ctx: discord.ApplicationContext, num_messages: int = 20, accent: str = None
):
    num_messages = min(num_messages, message_limit)

    chan = bot.get_channel(ctx.channel_id)
    if not chan:
        await ctx.respond("Sorry I don't have access to read this channel.")
        return

    await ctx.defer()

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

    config = get_config(ctx.guild_id)

    profile = config.get("profile", "")

    print(profile)

    if accent:
        profile += (
            f" Prioritize writing your summaries in an over the top way with an accent from or in the manner of {accent}. "
            "If the accent is something non-human like a dog, then instead summarize role-playing as that thing. "
        )

    summarizer = Summarizer(
        key=openai_api_key,
        model_override=config.get("model", None),
        profile=profile,
        # profile=config.get("profile", None),
    )

    summary = await summarizer.summarize(messages)
    await ctx.followup.send(summary)


bot.run(discord_api_key)
