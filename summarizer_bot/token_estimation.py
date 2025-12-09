"""
Token estimation utilities for LLM context management.

This module provides accurate token counting using Anthropic's count_tokens API
to prevent exceeding model context windows when building chat history.
"""

from typing import TYPE_CHECKING
from anthropic import AsyncAnthropic

if TYPE_CHECKING:
    from message import Message

# Context window limits for Claude Sonnet 4.5
MODEL_CONTEXT_WINDOW = 200000
MAX_OUTPUT_TOKENS = 2048
SAFETY_MARGIN = 5000  # Extra margin for overhead


class TokenCounter:
    """
    Handles token counting using Anthropic's count_tokens API.
    """

    def __init__(self, anthropic_client: AsyncAnthropic, model: str):
        """
        Initialize the token counter.

        Args:
            anthropic_client: The Anthropic client instance
            model: The model name to use for token counting
        """
        self.client = anthropic_client
        self.model = model

    async def count_tokens(self, messages: list["Message"], system_prompt: str = "") -> int:
        """
        Count the exact number of tokens for a list of messages using Anthropic's API.

        Args:
            messages: List of Message objects
            system_prompt: The system prompt (needed for accurate counting)

        Returns:
            Exact token count for all messages
        """
        if not messages:
            return 0

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
