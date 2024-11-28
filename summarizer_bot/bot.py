import os

import discord
from message import Message, UserProfile, parse_response
from summarizer import LLMClient

from loguru import logger
import json
from config import Config
import time


message_limit = 1000
root_user = ".namielle"


discord_api_key = os.environ.get("DISCORD_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")

print(f"discord_api_key {discord_api_key}")
print(f"openai_api_key {openai_api_key}")


intents = discord.Intents().default()
intents.members = True

bot = discord.Bot(intents=intents)
config = Config.try_init_from_file("config.json")
llm_client = LLMClient(openai_api_key)

with open("personas/mommy.json", "r") as f:
    persona = json.load(f)


def get_user_profiles(involved_users: set[discord.Member]) -> list[UserProfile]:
    user_profiles = []
    for user in involved_users:
        if config.has_user_config(user.id):
            user_info = config.get_user_config(user.id)["info"]
            user_profiles.append(UserProfile(user.display_name, user_info))
    return user_profiles


async def fetch_messages(channel_id, num_messages = message_limit) -> list[discord.Message]:
    num_messages = min(num_messages, message_limit)

    chan = bot.get_channel(channel_id)

    raw_messages = await chan.history(limit=num_messages).flatten()
    raw_messages.reverse()    
    
    return raw_messages


def process_messages(raw_messages: list[discord.Message], skip_bots: bool = True) -> tuple[list[Message], set[discord.Member]]:
    messages = []
    involved_users = set()

    for msg in raw_messages:
        # skip bots and empty messages
        if not msg.content or (skip_bots and msg.author.bot and not msg.reference):
            continue

        messages.append(Message(msg))
        involved_users.add(msg.author)

    return messages, involved_users


def concat_messages(messages: list[Message], involved_users: set[discord.Member]) -> tuple[str, str]:
    concat_msgs = "\n".join(str(msg) for msg in messages)
    concat_profs = "\n".join([str(prof) for prof in get_user_profiles(involved_users)])
    return concat_msgs, concat_profs


def build_json(messages: list[Message], involved_users: set[discord.Member]) -> tuple[list[dict], list[dict]]:
    return (
        [m.to_json() for m in messages],
        [p.to_json() for p in get_user_profiles(involved_users)]
    )



@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")


def make_sys_prompt() -> str: 
    time_of_day = time.strftime("%H:%M")
    day = time.strftime("%Y-%m-%d")

    return json.dumps(persona, indent=2)



def make_prompt(msg_str: str, message: discord.Message, user_profs_str: str | None = None) -> str:
    prompt = ""
    if user_profs_str:
        prompt += f"<USER PROFILES START>\n\n{user_profs_str}\n\n<USER PROFILES END>\n\n"

    prompt += (f"<CHAT HISTORY START>\n\n{msg_str}\n\n<CHAT HISTORY END>\n\n"
                f"You are responding to the following messsage:\n <MESSAGE START>\n{Message(message)}\n<MESSAGE END>"
                "Your response: ")
    
    return prompt

def make_prompt_json(messages: list[dict], profiles: list[dict], reply_message: discord.Message) -> str:
    data = {
        "chat_history" : messages,
    }
    if profiles:
        data["profiles"] = profiles

    to_reply = json.dumps(Message(reply_message).to_json())

    prompt = (
        f"{json.dumps(data)}\n\n"
        f"You are responding to the following message:\n"
        f"{to_reply}\n\n"
        f"Your response: "
    )
    
    return prompt

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
        
    if isinstance(message.channel, discord.channel.DMChannel) or (bot.user and bot.user.mentioned_in(message)):
        raw_messages = await fetch_messages(message.channel.id, num_messages=50)
        messages, involved_users = process_messages(raw_messages, False)

        # msg_str, user_profs_str = concat_messages(messages, involved_users)
        # prompt = make_prompt(msg_str, message, user_profs_str)

        messages_json, profiles_json = build_json(messages, involved_users)
        prompt = make_prompt_json(messages_json, profiles_json, message)

        sys_prompt = make_sys_prompt()
        
        # print(f"{prompt=}")
        
        response = await llm_client.generate(prompt, sys_prompt)
        response = parse_response(response, message.guild)

        # print(f"message={message.content}")
    
        await message.reply(response)
        

@bot.slash_command()
async def register_user(ctx: discord.ApplicationContext, info: str):
    await ctx.defer()

    user_config = config.get_server_config(ctx.author.id)

    info = info.strip()

    if len(info) > 128:
        await ctx.followup.send("Info too big")
        return
    
    if "\n" in info:
        await ctx.followup.send("newlines not allowed")
        return

    user_config["info"] = info

    await config.set_server_config(ctx.author.id, user_config)
    await ctx.followup.send("User configuration updated <3")


# @bot.slash_command()
# async def admin(ctx: discord.ApplicationContext, profile: str = None, model: str = None):
#     if ctx.author.name != root_user:
#         await ctx.send_response(
#             content="Sorry, you don't have permission to use this command!", 
#             ephemeral=True)
#         return
    
#     await ctx.defer()
    
#     server_config = config.get_server_config(ctx.guild_id)

#     if profile:
#         server_config["profile"] = profile
#     if model:
#         server_config["model"] = model

#     await config.set_server_config(ctx.guild_id, server_config)
#     await ctx.followup.send("Server config updated <3 <3")


@bot.slash_command()
async def summarize(ctx: discord.ApplicationContext, num_messages: int = 20, accent: str = None):
    await ctx.defer()

    raw_messages = await fetch_messages(ctx.channel_id, num_messages)
    messages, involved_users = process_messages(raw_messages)
    msg_str, user_profs_str = concat_messages(messages, involved_users)

    server_config = config.get_server_config(ctx.guild_id)
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
    
    summary = await llm_client.generate(prompt)
    await ctx.followup.send(summary)


def run():
    bot.run(discord_api_key)

if __name__ == "__main__":
    run()
