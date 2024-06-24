from openai import AsyncOpenAI
from message import Message


class Summarizer:
    def __init__(self, key: str, model_override: str = None, profile: str = None) -> None:
        self.client = AsyncOpenAI(api_key=key)
        self.model = model_override or "gpt-4o"
        self.base_system_prompt = (
            "You are a helpful tool for summarizing segments of chats in the popular chat service "
            "Discord. You should read the chat transcripts in full and provide a response that is "
            "purely a summary of the input and avoid mentioning any extra information except for "
            "any stylistic changes you are asked to provide. "
        )
        self.profile = profile


    def get_sys_prompt(self) -> str:
        if self.profile:
            return (f"{self.base_system_prompt} "
                    "Here are some additional instructions, please follow them as closely as possible: "
                    f"{self.profile} ")
        return self.base_system_prompt 

    async def summarize(self, msgs: list[Message]) -> str:
        concatenated_msgs = "".join(str(msg) for msg in msgs)

        if not concatenated_msgs:
            return "Sorry, there was nothing to summarize :)"

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.get_sys_prompt()},
                {"role": "user", "content": concatenated_msgs},
            ],
        )

        return response.choices[0].message.content
