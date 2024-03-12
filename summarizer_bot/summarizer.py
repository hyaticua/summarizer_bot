from openai import AsyncOpenAI
from message import Message


class Summarizer:
    def __init__(self, key) -> None:
        self.client = AsyncOpenAI(api_key=key)
        self.model = "gpt-3.5-turbo"
        self.system_prompt = (
            "You are a helpful tool for summarizing segments of chats in the popular chat service "
            "Discord. You should read the chat transcripts in full and provide a response that is "
            "purely a summary of the input and avoid mentioning any extra information. "
        )

    async def summarize(self, msgs: list[Message]) -> str:
        concatenated_msgs = "".join(str(msg) for msg in msgs)

        if not concatenated_msgs:
            return "Sorry, there was nothing to summarize :)"

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": concatenated_msgs},
            ],
        )

        return response.choices[0].message.content
