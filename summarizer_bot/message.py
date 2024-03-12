from dataclasses import dataclass
from typing_extensions import Self
import discord


@dataclass
class Message:
    author: str
    content: str

    @staticmethod
    def convert(msg: discord.Message) -> Self:
        author = msg.author
        if not isinstance(author, discord.Member):
            members = msg.guild.query_members(user_ids=[msg.author.id])
            author = members[0]

        return Message(
            author.nick,
            msg.content,
        )

    def __str__(self) -> str:
        return f"{self.author}:\n{self.content}\n"
