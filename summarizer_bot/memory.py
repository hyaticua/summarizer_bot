import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import aiofiles
from loguru import logger

MEMORIES_FILE = "memories.json"
MAX_MEMORIES_PER_GUILD = 50
MAX_CONTENT_LENGTH = 500
MAX_KEY_LENGTH = 100


@dataclass
class Memory:
    key: str
    content: str
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601


class MemoryStore:
    def __init__(self):
        self.memories: dict[int, list[Memory]] = {}  # guild_id -> memories
        self._load()

    def _load(self):
        """Sync load from memories.json at startup."""
        try:
            with open(MEMORIES_FILE, "r") as f:
                data = json.load(f)
            for guild_id_str, entries in data.items():
                guild_id = int(guild_id_str)
                self.memories[guild_id] = [Memory(**e) for e in entries]
            total = sum(len(v) for v in self.memories.values())
            logger.info("Loaded {} memories across {} guilds from {}", total, len(self.memories), MEMORIES_FILE)
        except FileNotFoundError:
            logger.info("No memories file found, starting fresh")
        except Exception as e:
            logger.error("Failed to load memories: {}", e)

    async def _save(self):
        """Async persist to memories.json via aiofiles."""
        data = {
            str(guild_id): [asdict(m) for m in memories]
            for guild_id, memories in self.memories.items()
        }
        async with aiofiles.open(MEMORIES_FILE, mode="w") as f:
            await f.write(json.dumps(data, indent=2))

    async def save_memory(self, guild_id: int, key: str, content: str) -> str:
        """Create or update a memory. Returns status string for tool result."""
        key = key.strip()
        content = content.strip()

        if not key:
            return "Error: key cannot be empty."
        if len(key) > MAX_KEY_LENGTH:
            return f"Error: key must be {MAX_KEY_LENGTH} characters or fewer (got {len(key)})."
        if not content:
            return "Error: content cannot be empty."
        if len(content) > MAX_CONTENT_LENGTH:
            return f"Error: content must be {MAX_CONTENT_LENGTH} characters or fewer (got {len(content)})."

        guild_memories = self.memories.setdefault(guild_id, [])
        now = datetime.now(timezone.utc).isoformat()

        # Check if key already exists (update)
        for m in guild_memories:
            if m.key == key:
                m.content = content
                m.updated_at = now
                await self._save()
                logger.info("Updated memory '{}' for guild {}", key, guild_id)
                return f"Updated memory '{key}'."

        # New memory — check limit
        if len(guild_memories) >= MAX_MEMORIES_PER_GUILD:
            return f"This server already has {MAX_MEMORIES_PER_GUILD} memories. Delete some before adding more."

        guild_memories.append(Memory(key=key, content=content, created_at=now, updated_at=now))
        await self._save()
        logger.info("Saved new memory '{}' for guild {}", key, guild_id)
        return f"Saved memory '{key}'."

    async def delete_memory(self, guild_id: int, key: str) -> str:
        """Delete a memory by key. Returns status string for tool result."""
        key = key.strip()
        guild_memories = self.memories.get(guild_id, [])

        for i, m in enumerate(guild_memories):
            if m.key == key:
                guild_memories.pop(i)
                await self._save()
                logger.info("Deleted memory '{}' for guild {}", key, guild_id)
                return f"Deleted memory '{key}'."

        return f"No memory found with key '{key}'."

    def get_memories(self, guild_id: int) -> list[Memory]:
        """Get all memories for a guild (for system prompt injection)."""
        return self.memories.get(guild_id, [])

    def format_for_prompt(self, guild_id: int) -> str:
        """Format memories as a text section for the system prompt.

        Returns empty string if no memories.
        """
        memories = self.get_memories(guild_id)
        if not memories:
            return ""

        lines = ["# Memories\n"]
        lines.append("You have the following saved memories about this server and its members. "
                      "You can save new memories with the save_memory tool or delete outdated ones with delete_memory.\n")
        for m in memories:
            lines.append(f"- {m.key}: {m.content}")

        return "\n".join(lines) + "\n"
