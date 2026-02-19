import json

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from message import Message
from discord_tools import DISCORD_TOOLS, _status_for_tool
from loguru import logger

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
WEB_FETCH_TOOL = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}
MAX_CONTINUATIONS = 3
MAX_TOOL_ROUNDS = 3

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
        logger.debug("generate() called (model={}, prompt_len={})", self.model, len(prompt))
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

        logger.debug("generate() response: {} tokens in, {} tokens out",
                      response.usage.input_tokens, response.usage.output_tokens)
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

        logger.debug("generate_as_chat_turns() called (model={}, turns={})", self.model, len(chat_turns))

        response = await self.client.messages.create(
            model=self.model,
            system=sys_prompt or default_sys_prompt,
            max_tokens=2048,
            messages=chat_turns
        )

        logger.debug("generate_as_chat_turns() response: {} tokens in, {} tokens out",
                      response.usage.input_tokens, response.usage.output_tokens)
        return response.content[0].text

    async def generate_as_chat_turns_with_search(self, messages: list[Message], sys_prompt: str, status_callback=None, tool_executor=None) -> str:
        """Generate a response using streaming with web search and Discord tool support.

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

        logger.info("Streaming chat request (model={}, turns={}, tools={})",
                     self.model, len(chat_turns), "yes" if tool_executor else "web-only")
        try:
            return await self._stream_with_search(chat_turns, sys_prompt, status_callback, tool_executor)
        except Exception as e:
            logger.warning("Streaming failed, falling back to non-streaming: {}", e)
            return await self.generate_as_chat_turns(messages, sys_prompt)

    async def _stream_with_search(self, chat_turns: list[dict], sys_prompt: str, status_callback=None, tool_executor=None) -> str:
        """Run a streaming request with web search and tool use, handling continuations."""
        turns = list(chat_turns)
        text = ""
        tool_rounds = 0

        for iteration in range(MAX_CONTINUATIONS + MAX_TOOL_ROUNDS + 1):
            response = await self._stream_single_request(turns, sys_prompt, status_callback, tool_executor)
            text = self._extract_text(response)

            logger.debug("Stream iteration {}: stop_reason={}, tokens_in={}, tokens_out={}",
                         iteration, response.stop_reason,
                         response.usage.input_tokens, response.usage.output_tokens)

            if response.stop_reason == "pause_turn":
                # Server-side tool continuation (web search or web fetch)
                logger.info("Server tool continuation (iteration {})", iteration)
                turns.append({"role": "assistant", "content": response.content})
                turns.append({"role": "user", "content": "Continue."})
            elif response.stop_reason == "tool_use" and tool_executor:
                tool_rounds += 1
                if tool_rounds > MAX_TOOL_ROUNDS:
                    logger.warning("Max tool rounds ({}) reached, returning partial response", MAX_TOOL_ROUNDS)
                    return text

                # Extract tool_use blocks and execute them
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool use: {} (round {})", block.name, tool_rounds)
                        if status_callback:
                            await status_callback(_status_for_tool(block.name, block.input))
                        result = await tool_executor.execute(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Append assistant message with tool_use blocks, then user message with results
                turns.append({"role": "assistant", "content": response.content})
                turns.append({"role": "user", "content": tool_results})
            else:
                logger.debug("Stream complete (stop_reason={})", response.stop_reason)
                return text

        # Hit max rounds — return what we have
        logger.warning("Hit max stream iterations ({}), returning partial response", MAX_CONTINUATIONS + MAX_TOOL_ROUNDS + 1)
        return text

    async def _stream_single_request(self, turns, sys_prompt, status_callback, tool_executor=None):
        """Execute a single streaming request, calling status_callback on stream events."""
        thinking_notified = False
        search_notified = False
        fetch_pending = False  # True while we're waiting for the fetch URL
        fetch_input_json = ""  # Accumulates partial JSON for the fetch input

        tools = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL]
        if tool_executor:
            tools.extend(DISCORD_TOOLS)

        async with self.client.messages.stream(
            model=self.model,
            system=sys_prompt or default_sys_prompt,
            max_tokens=2048,
            tools=tools,
            messages=turns,
        ) as stream:
            async for event in stream:
                if not status_callback:
                    continue
                if event.type == "content_block_start":
                    block = event.content_block
                    block_type = getattr(block, "type", None)
                    if not thinking_notified and block_type == "thinking":
                        thinking_notified = True
                        await status_callback("Thinking...")
                    elif block_type == "server_tool_use":
                        tool_name = getattr(block, "name", None)
                        if not search_notified and tool_name == "web_search":
                            search_notified = True
                            await status_callback("Searching the web...")
                        elif tool_name == "web_fetch":
                            fetch_pending = True
                            fetch_input_json = ""
                elif event.type == "content_block_delta" and fetch_pending:
                    delta = event.delta
                    if getattr(delta, "type", None) == "input_json_delta":
                        fetch_input_json += delta.partial_json
                elif event.type == "content_block_stop" and fetch_pending:
                    fetch_pending = False
                    # Extract URL from accumulated JSON
                    try:
                        url = json.loads(fetch_input_json).get("url", "")
                    except (json.JSONDecodeError, AttributeError):
                        url = ""
                    if url:
                        await status_callback(f"Fetching {url}...")
                    else:
                        await status_callback("Fetching a web page...")

            return await stream.get_final_message()

    @staticmethod
    def _extract_text(response) -> str:
        """Extract and concatenate all text blocks from a response, skipping tool blocks."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts) if parts else ""