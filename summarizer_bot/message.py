from dataclasses import dataclass
from typing_extensions import Self
import discord


@dataclass
class Message:
    author: str
    content: str

    @staticmethod
    def convert(msg: discord.Message) -> Self:
        return Message(
            msg.author.display_name,
            msg.content,
        )

    def __str__(self) -> str:
        return f"{self.author}:\n{self.content}\n"
