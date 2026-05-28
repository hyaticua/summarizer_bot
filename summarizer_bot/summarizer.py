import json
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic
from anthropic import APIStatusError
import httpx
from openai import AsyncOpenAI
try:
    from message import Message
except ImportError:
    from .message import Message

try:
    from discord_tools import _status_for_tool
except ImportError:
    from .discord_tools import _status_for_tool
from loguru import logger

@dataclass
class FileAttachment:
    data: bytes
    filename: str

@dataclass
class LLMResponse:
    text: str
    files: list[FileAttachment] = field(default_factory=list)

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
WEB_FETCH_TOOL = {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 3}
CODE_EXECUTION_TOOL = { "type": "code_execution_20250825", "name": "code_execution" }
MAX_CONTINUATIONS = 5
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
        self.client = AsyncAnthropic(
            api_key=key,
            timeout=httpx.Timeout(120, connect=10),
        )
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

    async def generate_as_chat_turns_with_search(self, messages: list[Message], sys_prompt: str, status_callback=None, tool_executor=None) -> LLMResponse:
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
        except APIStatusError:
            raise  # Don't retry on API errors (overloaded, rate limit, etc.)
        except Exception as e:
            logger.warning("Streaming failed, falling back to non-streaming: {}", e)
            text = await self.generate_as_chat_turns(messages, sys_prompt)
            return LLMResponse(text=text)

    async def _stream_with_search(self, chat_turns: list[dict], sys_prompt: str, status_callback=None, tool_executor=None) -> LLMResponse:
        """Run a streaming request with web search and tool use, handling continuations."""
        turns = list(chat_turns)
        text_parts: list[str] = []
        continuations = 0
        tool_rounds = 0
        all_file_ids: list[str] = []

        def add_text(part: str) -> None:
            if part:
                text_parts.append(part)

        max_iterations = MAX_CONTINUATIONS + MAX_TOOL_ROUNDS + 1
        for iteration in range(max_iterations):
            response = await self._stream_single_request(turns, sys_prompt, status_callback, tool_executor)
            add_text(self._extract_text(response))
            all_file_ids.extend(self._extract_file_ids(response))

            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            logger.debug("Stream iteration {}: stop_reason={}, tokens_in={}, tokens_out={}, cache_read={}, cache_write={}",
                         iteration, response.stop_reason,
                         response.usage.input_tokens, response.usage.output_tokens,
                         cache_read, cache_write)

            if response.stop_reason == "pause_turn":
                continuations += 1
                if continuations > MAX_CONTINUATIONS:
                    logger.warning("Max server continuations ({}) reached, asking model to wrap up", MAX_CONTINUATIONS)
                    turns.append({"role": "assistant", "content": self._sanitize_for_resubmit(response.content)})
                    turns.append({"role": "user", "content": "You've used all your tool turns. Please provide your final response now using only the information you've already gathered."})
                    # One last iteration to get the final response
                    final = await self._stream_single_request(turns, sys_prompt, status_callback, tool_executor)
                    add_text(self._extract_text(final))
                    all_file_ids.extend(self._extract_file_ids(final))
                    break
                # Server-side tool continuation (web search, web fetch, or code execution)
                logger.info("Server tool continuation ({}/{})", continuations, MAX_CONTINUATIONS)
                turns.append({"role": "assistant", "content": self._sanitize_for_resubmit(response.content)})
                turns.append({"role": "user", "content": f"Continue. ({MAX_CONTINUATIONS - continuations} tool turns remaining)"})
            elif response.stop_reason == "tool_use" and tool_executor:
                tool_rounds += 1
                if tool_rounds > MAX_TOOL_ROUNDS:
                    logger.warning("Max tool rounds ({}) reached, asking model to wrap up", MAX_TOOL_ROUNDS)
                    turns.append({"role": "assistant", "content": self._sanitize_for_resubmit(response.content)})
                    # Return tool errors so the model knows it can't use more tools
                    tool_errors = []
                    for block in response.content:
                        if block.type == "tool_use":
                            tool_errors.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "Tool limit reached. Please provide your final response now using only the information you've already gathered.",
                                "is_error": True,
                            })
                    turns.append({"role": "user", "content": tool_errors})
                    # One last iteration to get the final response
                    final = await self._stream_single_request(turns, sys_prompt, status_callback, tool_executor)
                    add_text(self._extract_text(final))
                    all_file_ids.extend(self._extract_file_ids(final))
                    break

                # Extract tool_use blocks and execute them
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool use: {} (round {}/{})", block.name, tool_rounds, MAX_TOOL_ROUNDS)
                        if status_callback:
                            await status_callback(_status_for_tool(block.name, block.input))
                        result = await tool_executor.execute(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Append assistant message with tool_use blocks, then user message with results
                turns.append({"role": "assistant", "content": self._sanitize_for_resubmit(response.content)})
                turns.append({"role": "user", "content": tool_results})
            elif response.stop_reason == "max_tokens":
                # Output cap hit mid-turn — possibly inside a server tool call. Sanitize
                # (drops any dangling server_tool_use) and ask the model to continue.
                logger.warning("Hit max_tokens (iteration {}), asking model to continue", iteration)
                sanitized = self._sanitize_for_resubmit(response.content)
                if not sanitized:
                    sanitized = [{"type": "text", "text": "(my previous response was cut off)"}]
                turns.append({"role": "assistant", "content": sanitized})
                turns.append({"role": "user", "content": "Your previous message was cut off before you finished. Please continue and complete your response."})
            else:
                logger.debug("Stream complete (stop_reason={})", response.stop_reason)
                break

        # If we ended up with no text, give the model one final chance to respond
        if not text_parts:
            logger.warning("No text after {} iterations, giving model one final turn to respond", iteration + 1)
            sanitized = self._sanitize_for_resubmit(response.content)
            if sanitized:
                turns.append({"role": "assistant", "content": sanitized})
            # Avoid two user turns in a row (the API requires alternating roles).
            if not turns or turns[-1]["role"] != "user":
                turns.append({"role": "user", "content": "Please provide your response now."})
            final = await self._stream_single_request(turns, sys_prompt, status_callback, tool_executor)
            add_text(self._extract_text(final))
            all_file_ids.extend(self._extract_file_ids(final))

        # Download any files produced by code execution
        logger.info("Collected {} file IDs across all iterations: {}", len(all_file_ids), all_file_ids)
        files = await self._download_files(all_file_ids) if all_file_ids else []

        text = "\n".join(text_parts)
        if not text and not files:
            logger.warning("Empty response even after final turn")
        return LLMResponse(text=text, files=files)

    async def _stream_single_request(self, turns, sys_prompt, status_callback, tool_executor=None):
        """Execute a single streaming request, calling status_callback on stream events."""
        thinking_notified = False
        search_notified = False
        code_notified = False
        fetch_pending = False  # True while we're waiting for the fetch URL
        fetch_input_json = ""  # Accumulates partial JSON for the fetch input

        tools = [CODE_EXECUTION_TOOL, WEB_SEARCH_TOOL, WEB_FETCH_TOOL]
        if tool_executor:
            tools.extend(tool_executor.get_available_tools())
        # Mark last tool for caching — caches all tool definitions as a prefix
        if tools:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

        async with self.client.beta.messages.stream(
            model=self.model,
            system=sys_prompt or default_sys_prompt,
            max_tokens=4096,
            tools=tools,
            betas=["files-api-2025-04-14"],
            messages=turns,
            cache_control={"type": "ephemeral"},  # auto-cache conversation prefix
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
                        logger.info("Server tool use: {}", tool_name)
                        if not search_notified and tool_name == "web_search":
                            search_notified = True
                            await status_callback("Searching the web...")
                        elif tool_name == "web_fetch":
                            fetch_pending = True
                            fetch_input_json = ""
                        elif not code_notified and tool_name in ("bash_code_execution", "text_editor_code_execution"):
                            code_notified = True
                            await status_callback("Running code...")
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

    _CODE_EXEC_RESULT_TYPES = (
        "code_execution_tool_result",
        "bash_code_execution_tool_result",
        "text_editor_code_execution_tool_result",
    )

    _TOOL_RESULT_TYPES = _CODE_EXEC_RESULT_TYPES + (
        "web_search_tool_result",
        "web_fetch_tool_result",
    )

    @classmethod
    def _sanitize_for_resubmit(cls, content):
        """Drop dangling server_tool_use blocks before re-submitting a turn.

        A ``max_tokens`` truncation can end an assistant turn right after a
        ``server_tool_use`` block but before its ``*_tool_result`` arrives.
        Re-submitting that turn makes the API reject the whole request
        ("tool use ... without a corresponding tool_result block"), so strip any
        server_tool_use whose id has no matching result block in the same turn.
        """
        result_ids = set()
        for block in content:
            if cls._get_field(block, "type") in cls._TOOL_RESULT_TYPES:
                tool_use_id = cls._get_field(block, "tool_use_id")
                if tool_use_id:
                    result_ids.add(tool_use_id)
        sanitized = []
        for block in content:
            if (cls._get_field(block, "type") == "server_tool_use"
                    and cls._get_field(block, "id") not in result_ids):
                logger.warning("Dropping dangling server_tool_use (id={}) before resubmit",
                               cls._get_field(block, "id"))
                continue
            sanitized.append(block)
        return sanitized

    @staticmethod
    def _get_content(block):
        """Get content from a code execution result block, handling both dict and object."""
        content = getattr(block, "content", None)
        return content

    @staticmethod
    def _get_field(obj, key, default=None):
        """Get a field from either a dict or an object."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _extract_text(cls, response) -> str:
        """Extract text blocks and code execution stdout from a response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
            elif block.type in cls._CODE_EXEC_RESULT_TYPES:
                content = cls._get_content(block)
                if content:
                    stdout = cls._get_field(content, "stdout", "")
                    if stdout:
                        parts.append(f"**Code output:**\n```\n{stdout}\n```")
        return "\n".join(parts) if parts else ""

    @classmethod
    def _extract_file_ids(cls, response) -> list[str]:
        """Extract file IDs from code execution tool result blocks."""
        file_ids = []
        for block in response.content:
            if block.type not in cls._CODE_EXEC_RESULT_TYPES:
                continue
            content = cls._get_content(block)
            logger.debug("Code exec result ({}): {}", block.type, content)
            if not content:
                continue
            output_list = cls._get_field(content, "content", [])
            if not output_list:
                continue
            for item in output_list:
                file_id = cls._get_field(item, "file_id")
                if file_id:
                    logger.info("Found file ID: {}", file_id)
                    file_ids.append(file_id)
        return file_ids

    async def _download_files(self, file_ids: list[str]) -> list[FileAttachment]:
        """Download files from Anthropic's Files API."""
        MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB Discord limit
        files = []
        for file_id in file_ids:
            try:
                metadata = await self.client.beta.files.retrieve_metadata(file_id)
                if metadata.size_bytes and metadata.size_bytes > MAX_FILE_SIZE:
                    logger.warning("Skipping file {} ({} bytes) — exceeds 8MB Discord limit",
                                   file_id, metadata.size_bytes)
                    continue
                resp = await self.client.beta.files.download(file_id)
                data = await resp.read()
                files.append(FileAttachment(data=data, filename=metadata.filename))
                logger.info("Downloaded file: {} ({} bytes)", metadata.filename, len(data))
            except Exception as e:
                logger.warning("Failed to download file {}: {}", file_id, e)
        return files