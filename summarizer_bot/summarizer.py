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
        self.model = model or "claude-sonnet-4-5-20250929"
        self.haiku_model = "claude-haiku-4-5"

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


        # print(chat_turns)
        # print("\n\n")

        response = await self.client.messages.create(
            model=self.model,
            system=sys_prompt or default_sys_prompt,
            max_tokens=2048,
            messages=chat_turns
        )

        # print(response)

        return response.content[0].text

    async def should_respond(self, recent_messages: list[Message], bot_name: str) -> bool:
        """Use Haiku to quickly decide if the bot should respond to recent conversation."""
        # Format recent messages for decision-making
        context = "\n".join([
            f"[{msg.author}]: {msg.text}" for msg in recent_messages[-10:]  # Last 10 messages max
        ])

        decision_prompt = f"""You are {bot_name}, a Discord bot. Based on the recent conversation below, should you naturally join in and respond?

Consider:
- Is the conversation relevant to something you could contribute to?
- Would it be natural for you to speak up here, or would it feel forced?
- Has enough time passed since you last spoke (if you did)?
- Is this an active conversation where your input would add value?

Recent conversation:
{context}

Respond with ONLY "yes" or "no" (lowercase)."""

        response = await self.client.messages.create(
            model=self.haiku_model,
            max_tokens=10,
            messages=[
                {
                    "role": "user",
                    "content": decision_prompt
                }
            ]
        )

        decision = response.content[0].text.strip().lower()
        return decision == "yes"