import discord
import re


def parse_content(message: discord.Message):
    pattern = r"<@(\d+)>"

    def replace_match(match):
        user_id = int(match.group(1))
        member = message.guild.get_member(user_id)
        return f"<@{member}>"
    
    return re.sub(pattern, replace_match, message.content)


def parse_response(response: str, guild: discord.Guild):
    pattern = r"<@([a-zA-Z0-9 _\-!#]+)>"

    def replace_match(match):
        display_name = match.group(1)
        member = discord.utils.find(lambda m: m.display_name == display_name, guild.members)
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

class Message:
    def __init__(self, msg: discord.Message):
        self.author = msg.author
        if isinstance(self.author, discord.User):
            # print(f"{self.author.id=}")
            self.author = msg.guild.get_member(self.author.id)

        self.author = msg.author.display_name
        self.content = parse_content(msg)

    def __str__(self) -> str:
        return f"{self.author}: {self.content}"
    
    def to_json(self) -> dict:
        obj = {
            "user" : self.author,
            "message" : self.content 
        }
        return obj
