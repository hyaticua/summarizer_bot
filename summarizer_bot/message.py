from dataclasses import dataclass
from typing_extensions import Self
import discord


@dataclass
class Message:
    author: str
    content: str

    @staticmethod
    def convert(msg: discord.Message, author: discord.Member | discord.User) -> Self:
        return Message(
            author.display_name,
            msg.content,
        )

    def __str__(self) -> str:
        return f"{self.author}:\n{self.content}\n"
