"""
Token estimation utilities for LLM context management.

This module provides both accurate API-based token counting and fast heuristic-based
estimation to prevent exceeding model context windows when building chat history.
"""

from typing import TYPE_CHECKING
from anthropic import AsyncAnthropic
import json

if TYPE_CHECKING:
    from message import Message

# Context window limits for Claude Sonnet 4.5
MODEL_CONTEXT_WINDOW = 200000
MAX_OUTPUT_TOKENS = 2048
SAFETY_MARGIN = 5000  # Extra margin for overhead

# Estimation constants for fast mode
CHARS_PER_TOKEN = 4  # Rough estimate: 1 token ≈ 4 characters
IMAGE_TOKENS = 1600  # Conservative estimate for a typical image (assumes ~2048x2048 max)


class TokenCounter:
    """
    Handles token counting using either Anthropic's API or fast estimation.
    """

    def __init__(self, anthropic_client: AsyncAnthropic, model: str, use_api: bool = True):
        """
        Initialize the token counter.

        Args:
            anthropic_client: The Anthropic client instance
            model: The model name to use for token counting
            use_api: If True, use API for accurate counting. If False, use fast estimation.
        """
        self.client = anthropic_client
        self.model = model
        self.use_api = use_api

    async def count_tokens(self, messages: list["Message"], system_prompt: str = "") -> int:
        """
        Count tokens for a list of messages.

        Uses either the API (accurate but slower) or estimation (fast but approximate).

        Args:
            messages: List of Message objects
            system_prompt: The system prompt (needed for accurate counting)

        Returns:
            Token count for all messages
        """
        if not messages:
            return 0

        if self.use_api:
            return await self._count_tokens_api(messages, system_prompt)
        else:
            return self._estimate_tokens(messages, system_prompt)

    async def _count_tokens_api(self, messages: list["Message"], system_prompt: str = "") -> int:
        """
        Count the exact number of tokens using Anthropic's API.

        Args:
            messages: List of Message objects
            system_prompt: The system prompt (needed for accurate counting)

        Returns:
            Exact token count for all messages
        """
        # Build chat turns in the same format as generate_as_chat_turns
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

        # Use Anthropic's token counting
        result = await self.client.messages.count_tokens(
            model=self.model,
            system=system_prompt,
            messages=chat_turns
        )

        return result.input_tokens

    def _estimate_tokens(self, messages: list["Message"], system_prompt: str = "") -> int:
        """
        Estimate tokens using fast heuristics (no API calls).

        Args:
            messages: List of Message objects
            system_prompt: The system prompt

        Returns:
            Estimated token count
        """
        total_tokens = 0

        # Estimate system prompt tokens
        total_tokens += len(system_prompt) // CHARS_PER_TOKEN

        # Estimate message tokens
        for msg in messages:
            if msg.from_self:
                # Bot's own messages are sent as plain text
                total_tokens += len(msg.text) // CHARS_PER_TOKEN
            else:
                # User messages are sent as JSON - estimate the JSON size
                json_str = json.dumps(msg.to_json())
                total_tokens += len(json_str) // CHARS_PER_TOKEN

            # Add image tokens
            total_tokens += len(msg.images) * IMAGE_TOKENS

        # Add some overhead for message structure
        total_tokens += len(messages) * 10

        return total_tokens

    def get_max_context_tokens(self) -> int:
        """
        Get the maximum number of tokens we should use for context.

        This accounts for:
        - The model's total context window
        - Reserved space for output
        - Safety margin for overhead

        Returns:
            Maximum tokens available for message context
        """
        return MODEL_CONTEXT_WINDOW - MAX_OUTPUT_TOKENS - SAFETY_MARGIN
