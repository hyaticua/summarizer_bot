import discord


class UserProfile:
    def __init__(self, user, info):
        self.user = user
        self.info = info

    def __str__(self) -> str:
        return f"{self.user}: {self.info}"

class Message:
    def __init__(self, msg: discord.Message):
        self.author = msg.author.display_name
        self.content = msg.content

    def __str__(self) -> str:
        return f"{self.author}: {self.content}"
