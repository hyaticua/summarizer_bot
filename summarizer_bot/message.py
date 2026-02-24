from dataclasses import dataclass
import discord
import re
import base64
import json
from discord.ext.commands import MemberConverter
from loguru import logger

def attempt_to_find_member(name: str, guild: discord.Guild):
    """
    Attempt to find a guild member by their display name.

    Handles various name formats and edge cases:
    - Display names with spaces, special characters, emojis
    - Nickname format
    - "Name (GlobalName)" format
    - Case-sensitive matching
    """
    # First try exact match on display_name (most common case)
    member = discord.utils.find(lambda m: m.display_name == name, guild.members)
    if member:
        return member

    # Try nickname (might be different from display_name in some cases)
    member = discord.utils.find(lambda m: m.nick and m.nick == name, guild.members)
    if member:
        return member

    # Try global_name
    member = discord.utils.find(lambda m: m.global_name and m.global_name == name, guild.members)
    if member:
        return member

    # Try username
    member = discord.utils.find(lambda m: m.name == name, guild.members)
    if member:
        return member

    # Try parsing "Name (OtherName)" format that the LLM sometimes generates
    pattern = r"^(.*?)\s+\((.*?)\)$"
    match = re.match(pattern, name)
    if match:
        candidates = [match.group(1).strip(), match.group(2).strip()]
        for candidate in candidates:
            member = discord.utils.find(
                lambda m, c=candidate: c in (m.display_name, m.nick, m.global_name, m.name),
                guild.members
            )
            if member:
                return member

    return None

def parse_content(message: discord.Message):
    content = message.content

    # Resolve user mentions: <@123> or <@!123> -> <@DisplayName>
    user_pattern = r"<@!?(\d+)>"

    def replace_user(match):
        user_id = int(match.group(1))
        member = message.guild.get_member(user_id)
        if not member:
            return match.group(0)
        return f"<@{member.display_name}>"

    content = re.sub(user_pattern, replace_user, content)

    # Resolve channel mentions: <#123> -> #channel-name
    channel_pattern = r"<#(\d+)>"

    def replace_channel(match):
        channel_id = int(match.group(1))
        channel = message.guild.get_channel(channel_id)
        if not channel:
            return match.group(0)
        return f"#{channel.name}"

    content = re.sub(channel_pattern, replace_channel, content)

    return content


def format_message_text(
    msg: discord.Message,
    max_length: int = 0,
    include_attachment_names: bool = False,
) -> str:
    """Shared formatting for a Discord message's text content.

    Handles mention resolution, reply context, optional truncation,
    and optional attachment filename listing.  Used by both the chat
    context (Message class) and the Discord tool output.
    """
    content = parse_content(msg) if msg.content else "[no text]"

    if max_length and len(content) > max_length:
        content = content[:max_length] + "..."

    # Reply context
    if msg.reference and msg.reference.resolved:
        ref = msg.reference.resolved
        if isinstance(ref, discord.Message):
            content = f"[replying to {ref.author.display_name}] {content}"
        else:
            # DeletedReferencedMessage
            content = f"[replying to deleted message] {content}"

    if include_attachment_names and msg.attachments:
        filenames = ", ".join(a.filename for a in msg.attachments)
        content += f" [attachments: {filenames}]"

    # Reactions (count-based, sync-safe)
    if msg.reactions:
        reaction_parts = []
        for reaction in msg.reactions:
            emoji = str(reaction.emoji)
            if reaction.count > 0:
                reaction_parts.append(f"{emoji} x{reaction.count}")
        if reaction_parts:
            content += f" [reactions: {', '.join(reaction_parts)}]"

    return content


def parse_response(response: str, guild: discord.Guild):
    pattern = r"@?<@?([^>]+)>"

    def replace_match(match):
        display_name = match.group(1)
        member = attempt_to_find_member(display_name, guild)
        if not member:
            logger.debug("Could not resolve mention '{}' to a guild member", display_name)
            return f"<@{display_name}>"

        logger.debug("Resolved mention '{}' -> {} (id={})", display_name, member.display_name, member.id)
        return f"<@{member.id}>"

    return re.sub(pattern, replace_match, response)


class UserProfile:
    def __init__(self, user, info):
        self.user = user
        self.info = info

    def __str__(self) -> str:
        return f"{self.user}: {self.info}"
    
    def to_json(self) -> dict:
        obj = {
            "user": self.user,
            "info": self.info
        }
        return obj

@dataclass
class Image:
    data: str
    content_type: str

    @classmethod
    async def create(cls, attachment: discord.Attachment) -> "Image":
        image_str = base64.b64encode(await attachment.read(use_cached=True)).decode()
        
        return cls(image_str, attachment.content_type)

class Message:
    def __init__(self, msg: discord.Message, from_self: bool = False):
        self.author = msg.author
        if isinstance(self.author, discord.User):
            self.author = msg.guild.get_member(self.author.id)

        self.author = msg.author.display_name
        self.text = format_message_text(msg)
        self.id = msg.id
        self.created_at = msg.created_at
        self.images: list[Image] = []
        self.reactions: list[tuple[str, list[str]]] = []
        self.from_self = from_self

    @classmethod
    async def create(cls, msg: discord.Message, from_self: bool = False) -> "Message":
        obj = cls(msg, from_self)

        for attachment in msg.attachments:
            if "image" in attachment.content_type:
                obj.images.append(await Image.create(attachment))

        # Fetch detailed reaction data (user names instead of counts)
        bot_id = msg.guild.me.id if msg.guild else None
        for reaction in msg.reactions:
            emoji_str = str(reaction.emoji)
            try:
                users_list = await reaction.users().flatten()
            except Exception:
                continue
            names = [u.display_name for u in users_list if not (bot_id and u.id == bot_id)]
            if names:
                obj.reactions.append((emoji_str, names))

        # Replace count-based reaction text with user-based format
        if obj.reactions:
            bracket_idx = obj.text.rfind(" [reactions:")
            if bracket_idx != -1:
                obj.text = obj.text[:bracket_idx]
            parts = [f"{emoji} {', '.join(names)}" for emoji, names in obj.reactions]
            obj.text += f" [reactions: {' | '.join(parts)}]"

        return obj
    
    def __str__(self) -> str:
        return f"{self.author}: {self.text}"
    
    def to_json(self) -> dict:
        obj = {
            "message_id": self.id,
            "created_at": str(self.created_at),
            "author" : self.author,
            "content" : self.text,
        }
        if self.reactions:
            obj["reactions"] = [
                {"emoji": emoji, "users": users}
                for emoji, users in self.reactions
            ]
        return obj
    
    def to_chat_turns(self) -> list[dict]:
        objs = []

        if self.text:
            text_obj = {
                "type": "text",
                "text": json.dumps(self.to_json()) if not self.from_self else self.text,
            }
            objs.append(text_obj)

        for image in self.images:
            img_obj = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.content_type,
                    "data": image.data,
                }
            }
            objs.append(img_obj)

        return objs
