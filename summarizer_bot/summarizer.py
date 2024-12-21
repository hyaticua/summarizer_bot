from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from message import Message
from itertools import chain

default_sys_prompt = (
    "You are a helpful tool for summarizing segments of chats. "
    "You should read the chat transcripts in full and provide a response that is "
    "purely a succinct summary of the input and avoid mentioning any extra information except for "
    "any stylistic changes or roleplaying you are asked to provide. "
)

class OpenAIClient:
    def __init__(self, key: str, model: str = None) -> None:
        self.client = AsyncOpenAI(api_key=key)
        self.model = model or "gpt-4o"

    async def generate(self, prompt: str, sys_prompt: str = None) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": sys_prompt or default_sys_prompt},
                {"role": "user", "content": prompt},
            ],
        )

        return response.choices[0].message.content

class AnthropicClient:
    def __init__(self, key, model: str = None) -> None:
        self.client = AsyncAnthropic(api_key=key)
        self.model = model or "claude-3-5-sonnet-20241022"

    async def generate(self, prompt: str, sys_prompt: str = None) -> str:
        response = await self.client.messages.create(
            model=self.model,
            system=sys_prompt or default_sys_prompt,
            max_tokens=2048,
            messages=[
                {
                     "role": "user",
                     "content" : [
                         {
                             "type": "text",
                             "text": prompt,
                         }
                     ]
                }                
            ]
        )

        # print(response)

        return response.content[0].text
    
    async def generate_as_chat_turns(self, messages: list[Message], sys_prompt: str) -> str:
        chat_turns = []
        for msg in messages:
            if msg.from_self:
                obj = {
                    "role": "assistant",
                    "content": msg.text
                }
            else:
                obj = {
                    "role": "user",
                    "content": msg.to_chat_turns()
                }
            chat_turns.append(obj)


        print(chat_turns)
        print("\n\n")

        response = await self.client.messages.create(
            model=self.model,
            system=sys_prompt or default_sys_prompt,
            max_tokens=2048,
            messages=chat_turns
        )

        # print(response)

        return response.content[0].text