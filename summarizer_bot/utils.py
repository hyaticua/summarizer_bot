import discord
from message import Message, UserProfile
import json
from datetime import datetime
from tz import ET



def concat_messages(messages: list[Message], user_profiles: list[UserProfile] = None) -> tuple[str, str]:
    concat_msgs = "\n".join(str(msg) for msg in messages)
    concat_profs = ""
    if user_profiles:
        concat_profs = "\n".join(str(prof) for prof in user_profiles)
    return concat_msgs, concat_profs


def build_json(messages: list[Message], user_profiles: list[UserProfile]) -> tuple[list[dict], list[dict]]:
    return (
        [m.to_json() for m in messages],
        [p.to_json() for p in user_profiles]
    )


def make_sys_prompt(guild: discord.Guild, persona: str, channel: discord.abc.Messageable = None, memories_text: str = "") -> list[dict]:
    """Build a system prompt as a list of content blocks for prompt caching.

    Returns two blocks:
    - Static persona (with cache_control) — cached across requests
    - Dynamic context (date/time/channel/memories) — changes per request, not cached
    """
    persona_text = persona.replace("{{BOT_NAME}}", guild.me.display_name)

    now = datetime.now(ET)
    context = f"# Current Context\n\nCurrent date: {now.strftime('%Y-%m-%d')}\nCurrent time: {now.strftime('%H:%M %Z')}\n"
    if channel:
        if isinstance(channel, discord.Thread):
            parent_name = channel.parent.name if channel.parent else "unknown"
            context += f"Source channel: thread #{channel.name} in #{parent_name}\n"
        elif hasattr(channel, "name"):
            context += f"Source channel: #{channel.name}\n"

    if memories_text:
        context += f"\n{memories_text}"

    return [
        {"type": "text", "text": persona_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": context},
    ]


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
        