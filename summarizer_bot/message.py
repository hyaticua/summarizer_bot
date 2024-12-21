from dataclasses import dataclass
import discord
import re
import base64
import json

def attempt_to_find_member(name: str, guild: discord.Guild):
    print(f"{name}")
    member = discord.utils.find(lambda m: m.nick == name, guild.members)
    if not member:
        pattern = "^(.*?)[ ]+\((.*?)\)$"
        match = re.match(pattern, name)
        if match:
            name = match.group(1)
            global_name = match.group(2)

            member = discord.utils.find(lambda m: m.name == name, guild.members)

    return member

def parse_content(message: discord.Message):
    pattern = r"<@(\d+)>"

    def replace_match(match):
        user_id = int(match.group(1))
        member = message.guild.get_member(user_id)
        return f"<@{member}>"
    
    return re.sub(pattern, replace_match, message.content)


def parse_response(response: str, guild: discord.Guild):
    pattern = r"<@(.*)>"

    def replace_match(match):
        display_name = match.group(1)
        # member = discord.utils.find(lambda m: m.nick == display_name, guild.members)
        member = attempt_to_find_member(display_name, guild)
        # print(f"{display_name=} {member=}")

        if not member: 
            return f"<@{display_name}>"

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


class Message:
    def __init__(self, msg: discord.Message, from_self: bool = False):
        self.author = msg.author
        if isinstance(self.author, discord.User):
            # print(f"{self.author.id=}")
            self.author = msg.guild.get_member(self.author.id)

        self.author = msg.author.display_name
        self.text = parse_content(msg)
        self.id = msg.id
        self.images: list[Image] = []
        self.from_self = from_self

    @classmethod
    async def create(cls, msg: discord.Message, from_self: bool = False) -> "Message":
        obj = cls(msg, from_self)

        for attachment in msg.attachments:
            if "image" in attachment.content_type:
                image_str = base64.b64encode(await attachment.read(use_cached=True)).decode()
                image = Image(image_str, attachment.content_type)
                obj.images.append(image)
        return obj

    def __str__(self) -> str:
        return f"{self.author}: {self.text}"
    
    def to_json(self) -> dict:
        obj = {
            "message_id": self.id,
            "author" : self.author,
            "content" : self.text,
        }
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
