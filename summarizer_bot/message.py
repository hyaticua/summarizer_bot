import discord
import re

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
