from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from message import Message
from loguru import logger

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
MAX_CONTINUATIONS = 3

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
        self.model = model or "claude-sonnet-4-6"

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

    async def generate_as_chat_turns_with_search(self, messages: list[Message], sys_prompt: str, status_callback=None) -> str:
        """Generate a response using streaming with web search tool support.

        Uses streaming to detect web search events mid-response and calls
        status_callback to update the user. Falls back to generate_as_chat_turns
        if streaming fails.
        """
        chat_turns = []
        for msg in messages:
            if msg.from_self:
                chat_turns.append({"role": "assistant", "content": msg.text})
            else:
                chat_turns.append({"role": "user", "content": msg.to_chat_turns()})

        try:
            return await self._stream_with_search(chat_turns, sys_prompt, status_callback)
        except Exception as e:
            logger.warning(f"Streaming web search failed, falling back to non-streaming: {e}")
            return await self.generate_as_chat_turns(messages, sys_prompt)

    async def _stream_with_search(self, chat_turns: list[dict], sys_prompt: str, status_callback=None) -> str:
        """Run a streaming request with web search, handling pause_turn continuations."""
        turns = list(chat_turns)
        text = ""

        for _ in range(MAX_CONTINUATIONS + 1):
            response = await self._stream_single_request(turns, sys_prompt, status_callback)
            text = self._extract_text(response)

            if response.stop_reason != "pause_turn":
                return text

            # Continue the conversation: append assistant response, then empty user turn
            turns.append({"role": "assistant", "content": response.content})
            turns.append({"role": "user", "content": "Continue."})

        # Hit max continuations — return what we have
        return text

    async def _stream_single_request(self, turns, sys_prompt, status_callback):
        """Execute a single streaming request, calling status_callback on stream events."""
        thinking_notified = False
        search_notified = False

        async with self.client.messages.stream(
            model=self.model,
            system=sys_prompt or default_sys_prompt,
            max_tokens=2048,
            tools=[WEB_SEARCH_TOOL],
            messages=turns,
        ) as stream:
            async for event in stream:
                if not status_callback or event.type != "content_block_start":
                    continue
                block_type = getattr(event.content_block, "type", None)
                if not thinking_notified and block_type == "thinking":
                    thinking_notified = True
                    await status_callback("Thinking...")
                elif not search_notified and block_type == "server_tool_use":
                    search_notified = True
                    await status_callback("Searching the web...")

            return await stream.get_final_message()

    @staticmethod
    def _extract_text(response) -> str:
        """Extract and concatenate all text blocks from a response, skipping tool blocks."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts) if parts else ""