from openai import AsyncOpenAI
from message import Message



class LLMClient:
    def __init__(self, key: str, model: str = None) -> None:
        self.client = AsyncOpenAI(api_key=key)
        self.model = model or "gpt-4o"
        self.system_prompt = (
            "You are a helpful tool for summarizing segments of chats. "
            "You should read the chat transcripts in full and provide a response that is "
            "purely a succinct summary of the input and avoid mentioning any extra information except for "
            "any stylistic changes or roleplaying you are asked to provide. "
        )

    async def generate(self, prompt: str, sys_prompt: str = None) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": sys_prompt or self.system_prompt},
                {"role": "user", "content": prompt},
            ],
        )

        return response.choices[0].message.content
