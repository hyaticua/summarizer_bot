from openai import AsyncOpenAI
from summarizer_bot.message import Message

class Summarizer:
    def __init__(self, api_key) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
        )
        self.model = "gpt-3.5-turbo"
        self.system_prompt = (
            "You are a helpful tool for summarizing segments of chats in the popular chat service "
            "Discord. You should read the chat transcripts in full and provide a response that is "
            "purely a summary of the input and avoid mentioning any extra information. "
        )
    
    async def summarize(self, msgs: list[Message]) -> str:
        concatenated_msgs = "".join(str(msg) for msg in msgs)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": concatenated_msgs},
            ]
        )

        return response

