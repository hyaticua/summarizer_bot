import re
from datetime import datetime, timedelta, timezone

import discord
from dateutil import parser as dateutil_parser
from loguru import logger

try:
    from tz import ET
except ImportError:
    from .tz import ET

try:
    from message import attempt_to_find_member, format_message_text
except ImportError:
    from .message import attempt_to_find_member, format_message_text

try:
    from memory import MemoryStore
except ImportError:
    from .memory import MemoryStore

SCAN_LIMIT = 500   # Max messages to scan from Discord when filtering
BATCH_SIZE = 100    # Messages per Discord API call (Discord's max)

ALL_DISCORD_TOOLS = [
    {
        "name": "get_server_members",
        "description": "See who is in the Discord server. Can show all members, members in voice channels, or members recently active in a specific channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["all", "voice", "channel"],
                    "description": "Filter type: 'all' lists server members, 'voice' lists members in voice channels, 'channel' lists recently active members in a text channel."
                },
                "channel_name": {
                    "type": "string",
                    "description": "Channel name to filter by. Required when filter is 'channel', optional for 'voice' to check a specific voice channel."
                }
            },
            "required": ["filter"]
        }
    },
    {
        "name": "list_channels",
        "description": "List all channels in the Discord server, organized by category. Shows channel types and voice channel occupancy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_threads": {
                    "type": "boolean",
                    "description": "Whether to include active threads. Defaults to false."
                }
            },
            "required": []
        }
    },
    {
        "name": "read_channel_history",
        "description": "Read recent messages from a channel or thread. Supports optional filters to search for specific messages. All filters combine with AND logic. When filters are active, up to 500 messages are scanned to find matches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Name of the channel or thread to read from."
                },
                "num_messages": {
                    "type": "integer",
                    "description": "Number of matching messages to return (default 25, max 50)."
                },
                "author": {
                    "type": "string",
                    "description": "Filter to messages from a specific user. Fuzzy-matched by display name, nickname, or username."
                },
                "contains": {
                    "type": "string",
                    "description": "Filter to messages containing this keyword or phrase (case-insensitive)."
                },
                "before": {
                    "type": "string",
                    "description": "Only messages before this time. Accepts relative ('yesterday', '2 hours ago', 'last week') or absolute ('2024-01-15') expressions."
                },
                "after": {
                    "type": "string",
                    "description": "Only messages after this time. Accepts relative ('yesterday', '2 hours ago', 'last week') or absolute ('2024-01-15') expressions."
                },
                "has_attachments": {
                    "type": "boolean",
                    "description": "If true, only include messages that have file attachments."
                },
                "exclude_bots": {
                    "type": "boolean",
                    "description": "If true, exclude messages from bot accounts."
                }
            },
            "required": ["channel_name"]
        }
    },
    {
        "name": "react_to_message",
        "description": "React to one or more messages with emoji. Supports single reaction (message_id + emoji) or batch mode (reactions array). Use this to express acknowledgment, agreement, humor, or emotion in response to messages — even when not explicitly asked to react.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Default channel name. Used for single-reaction mode and as default for batch items that omit channel_name."
                },
                "message_id": {
                    "type": "string",
                    "description": "The ID of the message to react to (single mode, ignored if reactions is provided)."
                },
                "emoji": {
                    "type": "string",
                    "description": "Unicode emoji (e.g. '👍') or custom emoji name (single mode, ignored if reactions is provided)."
                },
                "reactions": {
                    "type": "array",
                    "description": "Batch mode: array of reactions to add. Max 20.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "channel_name": {
                                "type": "string",
                                "description": "Channel name (optional, defaults to top-level channel_name)."
                            },
                            "message_id": {
                                "type": "string",
                                "description": "The ID of the message to react to."
                            },
                            "emoji": {
                                "type": "string",
                                "description": "Unicode emoji or custom emoji name."
                            }
                        },
                        "required": ["message_id", "emoji"]
                    }
                }
            },
            "required": ["channel_name"]
        }
    },
    {
        "name": "delete_messages",
        "description": "Delete messages in a channel. Can delete specific messages by ID (single or batch), or delete your own recent messages by count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Name of the channel to delete messages from."
                },
                "message_id": {
                    "type": "string",
                    "description": "Specific message ID to delete. If omitted, deletes your own recent messages."
                },
                "message_ids": {
                    "type": "array",
                    "description": "Batch mode: array of message IDs to delete. Max 10. If both message_id and message_ids are provided, they are merged and deduplicated.",
                    "items": {
                        "type": "string"
                    }
                },
                "count": {
                    "type": "integer",
                    "description": "Number of your own recent messages to delete (default 1, max 5). Only used when no message IDs are provided."
                }
            },
            "required": ["channel_name"]
        }
    },
    {
        "name": "timeout_member",
        "description": "Temporarily timeout a server member, preventing them from sending messages or joining voice channels for the specified duration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {
                    "type": "string",
                    "description": "Name of the member to timeout. Fuzzy-matched by display name, nickname, or username."
                },
                "duration": {
                    "type": "string",
                    "description": "How long to timeout the member. Examples: '5 minutes', '1 hour', '30 seconds', '1 day'."
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the timeout, shown in the Discord audit log."
                }
            },
            "required": ["member", "duration"]
        }
    },
    {
        "name": "schedule_message",
        "description": "Schedule a message or dynamic prompt to be sent in a channel at a future time. Static messages are sent as-is. Dynamic prompts are processed through the LLM at execution time with full tool access (web search, code execution, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Name of the channel to send the message in."
                },
                "time": {
                    "type": "string",
                    "description": "When to execute. Examples: 'in 2 hours', 'tomorrow at 9am', '2026-03-01 14:00'."
                },
                "type": {
                    "type": "string",
                    "enum": ["static", "dynamic"],
                    "description": "Static sends content as-is. Dynamic processes content as an LLM prompt at execution time with full tool access."
                },
                "content": {
                    "type": "string",
                    "description": "The message text (static) or prompt to process at execution time (dynamic)."
                },
                "reason": {
                    "type": "string",
                    "description": "Why this is being scheduled."
                }
            },
            "required": ["channel_name", "time", "type", "content", "reason"]
        }
    },
    {
        "name": "manage_scheduled",
        "description": "List or cancel scheduled tasks for this server.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "cancel"],
                    "description": "Whether to list all scheduled tasks or cancel a specific one."
                },
                "task_id": {
                    "type": "string",
                    "description": "The task ID to cancel. Required when action is 'cancel'."
                }
            },
            "required": ["action"]
        }
    },
]

MEMORY_TOOLS = [
    {
        "name": "save_memory",
        "description": "Save a memory about this server, its members, or anything worth remembering across conversations. If a memory with the same key exists, it will be updated.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "A short topic label for this memory (e.g. 'alice_birthday', 'movie_night_schedule'). Max 100 characters."
                },
                "content": {
                    "type": "string",
                    "description": "The content to remember. Max 500 characters."
                }
            },
            "required": ["key", "content"]
        }
    },
    {
        "name": "delete_memory",
        "description": "Delete a saved memory by its key. Use this to remove outdated or incorrect memories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The key of the memory to delete."
                }
            },
            "required": ["key"]
        }
    },
]

# Maps tool names to the guild-level Discord permissions required to expose that tool.
# Empty list means no special permission needed (available by default).
TOOL_PERMISSIONS = {
    "get_server_members": [],
    "list_channels": [],
    "read_channel_history": [],
    "delete_messages": [],               # bot can always delete own; manage_messages checked per-message
    "timeout_member": ["moderate_members"],
    "schedule_message": [],
    "manage_scheduled": [],
    "react_to_message": [],
}


def _parse_time_expression(expr: str) -> datetime | None:
    """Parse a human-readable time expression into a timezone-aware datetime.

    Supports:
    - Relative: "yesterday", "today", "last week", "last month", "N units ago"
    - Absolute: anything dateutil can parse (e.g. "2024-01-15", "Jan 15 2024")

    Returns None if the expression cannot be parsed.
    Naive datetimes are assumed to be in Eastern time.
    """
    expr = expr.strip().lower()
    now = datetime.now(ET)

    # Hardcoded relative expressions
    if expr == "yesterday":
        return now - timedelta(days=1)
    if expr == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if expr == "last week":
        return now - timedelta(weeks=1)
    if expr == "last month":
        return now - timedelta(days=30)

    # "N units ago" pattern
    match = re.match(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", expr)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        unit_map = {
            "second": timedelta(seconds=1),
            "minute": timedelta(minutes=1),
            "hour": timedelta(hours=1),
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
            "month": timedelta(days=30),
            "year": timedelta(days=365),
        }
        return now - unit_map[unit] * amount

    # Absolute date/time via dateutil
    try:
        parsed = dateutil_parser.parse(expr)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ET)
        return parsed
    except (ValueError, OverflowError):
        return None


def _status_for_tool(name: str, tool_input: dict) -> str:
    """Return a user-facing status string for a tool invocation."""
    if name == "get_server_members":
        channel = tool_input.get("channel_name")
        if channel:
            return f"Checking who's in #{channel}..."
        filt = tool_input.get("filter", "all")
        if filt == "voice":
            return "Checking voice channels..."
        return "Checking server members..."
    elif name == "list_channels":
        return "Listing server channels..."
    elif name == "read_channel_history":
        channel = tool_input.get("channel_name", "unknown")
        filters = _describe_active_filters(tool_input)
        if filters:
            return f"Searching #{channel} for {filters}..."
        return f"Reading messages from #{channel}..."
    elif name == "delete_messages":
        channel = tool_input.get("channel_name", "unknown")
        # Count IDs from both message_id and message_ids
        id_count = 0
        if tool_input.get("message_id"):
            id_count += 1
        if tool_input.get("message_ids"):
            id_count += len(tool_input["message_ids"])
        if id_count > 1:
            return f"Deleting {id_count} messages..."
        if id_count == 1:
            return "Deleting a message..."
        return f"Deleting messages in #{channel}..."
    elif name == "timeout_member":
        member = tool_input.get("member", "someone")
        return f"Timing out {member}..."
    elif name == "schedule_message":
        channel = tool_input.get("channel_name", "unknown")
        time_str = tool_input.get("time", "")
        return f"Scheduling a message in #{channel} for {time_str}..."
    elif name == "manage_scheduled":
        action = tool_input.get("action", "list")
        if action == "cancel":
            return "Cancelling a scheduled task..."
        return "Listing scheduled tasks..."
    elif name == "react_to_message":
        reactions = tool_input.get("reactions")
        if reactions and len(reactions) > 1:
            return f"Reacting to {len(reactions)} messages..."
        return "Reacting to a message..."
    elif name == "save_memory":
        key = tool_input.get("key", "")
        return f"Saving memory '{key}'..."
    elif name == "delete_memory":
        key = tool_input.get("key", "")
        return f"Deleting memory '{key}'..."
    return "Using a tool..."


def _describe_active_filters(tool_input: dict) -> str:
    """Build a short human-readable description of active filters."""
    parts = []
    if tool_input.get("author"):
        parts.append(f"messages from {tool_input['author']}")
    if tool_input.get("contains"):
        parts.append(f"containing '{tool_input['contains']}'")
    if tool_input.get("after") or tool_input.get("before"):
        time_parts = []
        if tool_input.get("after"):
            time_parts.append(f"after {tool_input['after']}")
        if tool_input.get("before"):
            time_parts.append(f"before {tool_input['before']}")
        parts.append(" ".join(time_parts))
    if tool_input.get("has_attachments"):
        parts.append("with attachments")
    if tool_input.get("exclude_bots"):
        parts.append("excluding bots")
    return ", ".join(parts)


def _parse_duration(expr: str) -> timedelta | None:
    """Parse a human-readable duration string into a timedelta.

    Supports "N unit(s)" patterns like "5 minutes", "1 hour", "30 seconds", "2 days".
    Returns None if the expression cannot be parsed.
    """
    expr = expr.strip().lower()
    match = re.match(r"(\d+)\s+(second|minute|hour|day|week)s?$", expr)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    unit_map = {
        "second": timedelta(seconds=1),
        "minute": timedelta(minutes=1),
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
        "week": timedelta(weeks=1),
    }
    return unit_map[unit] * amount


def _format_batch_results(results: list[tuple[bool, str]], action_verb: str) -> str:
    """Format batch operation results into a summary string.

    For a single item, returns the plain message (matching old single-item behavior).
    For multiple items, returns a summary like "Reacted 4/5:\n  OK: ...\n  FAILED: ...".
    """
    if len(results) == 1:
        return results[0][1]

    ok = [msg for success, msg in results if success]
    failed = [msg for success, msg in results if not success]
    parts = [f"{action_verb} {len(ok)}/{len(results)}:"]
    for msg in ok:
        parts.append(f"  OK: {msg}")
    for msg in failed:
        parts.append(f"  FAILED: {msg}")
    return "\n".join(parts)


class DiscordToolExecutor:
    def __init__(self, guild: discord.Guild, bot: discord.Bot, requesting_user: str = "unknown", memory_store: MemoryStore | None = None):
        self.guild = guild
        self.bot = bot
        self.requesting_user = requesting_user
        self.memory_store = memory_store
        self.active_message_id: int | None = None  # status message to exclude from deletions

    def get_available_tools(self) -> list[dict]:
        """Return tool definitions filtered by the bot's guild permissions."""
        perms = self.guild.me.guild_permissions
        available = []
        for tool in ALL_DISCORD_TOOLS:
            required = TOOL_PERMISSIONS.get(tool["name"], [])
            if all(getattr(perms, perm, False) for perm in required):
                available.append(tool)
        if self.memory_store is not None:
            available.extend(MEMORY_TOOLS)
        return available

    async def execute(self, name: str, tool_input: dict) -> str:
        """Execute a tool by name and return the result as a string."""
        logger.info(f"Tool execute: {name} with input: {tool_input}")
        try:
            if name == "get_server_members":
                result = await self._get_server_members(tool_input)
            elif name == "list_channels":
                result = await self._list_channels(tool_input)
            elif name == "read_channel_history":
                result = await self._read_channel_history(tool_input)
            elif name == "delete_messages":
                result = await self._delete_messages(tool_input)
            elif name == "timeout_member":
                result = await self._timeout_member(tool_input)
            elif name == "schedule_message":
                result = await self._schedule_message(tool_input)
            elif name == "manage_scheduled":
                result = await self._manage_scheduled(tool_input)
            elif name == "react_to_message":
                result = await self._react_to_message(tool_input)
            elif name == "save_memory":
                result = await self._save_memory(tool_input)
            elif name == "delete_memory":
                result = await self._delete_memory(tool_input)
            else:
                result = f"Unknown tool: {name}"
            logger.info(f"Tool result ({name}): {result[:500]}{'...' if len(result) > 500 else ''}")
            return result
        except Exception as e:
            logger.error(f"Tool execution error ({name}): {e}", exc_info=True)
            return f"Error executing {name}: {str(e)}"

    async def _get_server_members(self, tool_input: dict) -> str:
        filt = tool_input.get("filter", "all")

        if filt == "all":
            members = self.guild.members
            total = self.guild.member_count or len(members)
            lines = []
            for m in members[:200]:
                status_parts = []
                if m.bot:
                    status_parts.append("bot")
                if m.voice and m.voice.channel:
                    status_parts.append(f"in voice: #{m.voice.channel.name}")
                suffix = f" ({', '.join(status_parts)})" if status_parts else ""
                lines.append(f"- {m.display_name}{suffix}")
            header = f"Server members ({len(lines)} shown, {total} total):\n"
            return header + "\n".join(lines)

        elif filt == "voice":
            channel_name = tool_input.get("channel_name")
            if channel_name:
                channel = self._fuzzy_find_channel(channel_name, channel_types=["voice", "stage_voice"])
                if isinstance(channel, str):
                    return channel  # error message
                members = channel.members
                if not members:
                    return f"No one is in #{channel.name} right now."
                lines = [f"- {m.display_name}" for m in members]
                return f"Members in #{channel.name}:\n" + "\n".join(lines)
            else:
                lines = []
                for vc in self.guild.voice_channels + self.guild.stage_channels:
                    if vc.members:
                        member_names = ", ".join(m.display_name for m in vc.members)
                        lines.append(f"#{vc.name}: {member_names}")
                if not lines:
                    return "No one is in any voice channel right now."
                return "Members in voice channels:\n" + "\n".join(lines)

        elif filt == "channel":
            channel_name = tool_input.get("channel_name")
            if not channel_name:
                return "Error: channel_name is required when filter is 'channel'."
            channel = self._fuzzy_find_channel(channel_name, channel_types=["text", "forum"])
            if isinstance(channel, str):
                return channel  # error message
            try:
                messages = await channel.history(limit=50).flatten()
            except discord.Forbidden:
                return f"I don't have permission to read #{channel.name}."
            authors = {}
            for msg in messages:
                if msg.author.id not in authors and not msg.author.bot:
                    authors[msg.author.id] = msg.author.display_name
            if not authors:
                return f"No recent non-bot activity in #{channel.name}."
            lines = [f"- {name}" for name in authors.values()]
            return f"Recently active members in #{channel.name}:\n" + "\n".join(lines)

        return f"Unknown filter: {filt}"

    async def _list_channels(self, tool_input: dict) -> str:
        include_threads = tool_input.get("include_threads", False)
        lines = []

        # Categorized channels
        for category in self.guild.categories:
            lines.append(f"\n**{category.name}**")
            for ch in category.channels:
                lines.append(self._format_channel(ch))

        # Uncategorized channels
        uncategorized = [ch for ch in self.guild.channels if ch.category is None and not isinstance(ch, discord.CategoryChannel)]
        if uncategorized:
            lines.append("\n**Uncategorized**")
            for ch in uncategorized:
                lines.append(self._format_channel(ch))

        if include_threads:
            threads = self.guild.threads
            if threads:
                lines.append("\n**Active Threads**")
                for thread in threads:
                    parent = thread.parent.name if thread.parent else "unknown"
                    lines.append(f"  - #{thread.name} (thread in #{parent})")

        return "Server channels:" + "\n".join(lines)

    def _format_channel(self, ch) -> str:
        if isinstance(ch, discord.VoiceChannel):
            member_count = len(ch.members)
            occupancy = f" — {member_count} member{'s' if member_count != 1 else ''}" if member_count > 0 else ""
            return f"  - #{ch.name} (voice{occupancy})"
        elif isinstance(ch, discord.StageChannel):
            member_count = len(ch.members)
            occupancy = f" — {member_count} member{'s' if member_count != 1 else ''}" if member_count > 0 else ""
            return f"  - #{ch.name} (stage{occupancy})"
        elif isinstance(ch, discord.ForumChannel):
            return f"  - #{ch.name} (forum)"
        else:
            return f"  - #{ch.name} (text)"

    async def _read_channel_history(self, tool_input: dict) -> str:
        channel_name = tool_input.get("channel_name")
        if not channel_name:
            return "Error: channel_name is required."

        num_messages = min(tool_input.get("num_messages", 25), 50)

        channel = self._fuzzy_find_channel(channel_name)
        if isinstance(channel, str):
            return channel  # error message

        # Check permissions
        me = self.guild.me
        perms = channel.permissions_for(me)
        if not perms.read_message_history:
            return f"I don't have permission to read message history in #{channel.name}."

        has_filters = self._has_filters(tool_input)

        if not has_filters:
            # Fast path: no filters, single fetch
            return await self._read_unfiltered(channel, num_messages)

        # Filtered path: validate inputs, then batch-fetch with filtering
        # Resolve author
        author_member = None
        if tool_input.get("author"):
            author_member = attempt_to_find_member(tool_input["author"], self.guild)
            if author_member is None:
                return f"Could not find a member matching '{tool_input['author']}' in this server."

        # Parse time filters
        before_dt = None
        after_dt = None
        if tool_input.get("before"):
            before_dt = _parse_time_expression(tool_input["before"])
            if before_dt is None:
                return f"Could not parse time expression: '{tool_input['before']}'. Try 'yesterday', '2 hours ago', or '2024-01-15'."
        if tool_input.get("after"):
            after_dt = _parse_time_expression(tool_input["after"])
            if after_dt is None:
                return f"Could not parse time expression: '{tool_input['after']}'. Try 'yesterday', '2 hours ago', or '2024-01-15'."
        if before_dt and after_dt and after_dt >= before_dt:
            return "Error: 'after' must be earlier than 'before'."

        # Batch-fetch with filtering
        matches = []
        scanned = 0
        cursor_before = before_dt  # Pass to Discord API for server-side filtering

        try:
            while scanned < SCAN_LIMIT and len(matches) < num_messages:
                fetch_limit = min(BATCH_SIZE, SCAN_LIMIT - scanned)
                kwargs = {"limit": fetch_limit}
                if cursor_before:
                    kwargs["before"] = cursor_before
                if after_dt:
                    kwargs["after"] = after_dt

                batch = await channel.history(**kwargs).flatten()
                if not batch:
                    break

                scanned += len(batch)

                for msg in batch:
                    if self._matches_filters(msg, author_member, tool_input):
                        matches.append(msg)
                        if len(matches) >= num_messages:
                            break

                # Cursor-based pagination: move cursor to oldest message in batch
                oldest = batch[-1]
                cursor_before = oldest.created_at

                if len(batch) < fetch_limit:
                    break  # No more messages available

        except discord.Forbidden:
            return f"I don't have permission to read #{channel.name}."

        if not matches:
            return self._no_results_message(channel.name, tool_input, scanned)

        return self._format_history_output(channel.name, matches, tool_input, scanned)

    async def _read_unfiltered(self, channel, num_messages: int) -> str:
        """Fast path: fetch recent messages with no filtering."""
        try:
            messages = await channel.history(limit=num_messages).flatten()
        except discord.Forbidden:
            return f"I don't have permission to read #{channel.name}."

        if not messages:
            return f"No recent messages in #{channel.name}."

        messages.reverse()  # chronological order

        lines = []
        total_chars = 0
        max_total = 4000
        for msg in messages:
            content = format_message_text(msg, max_length=200, include_attachment_names=True)
            timestamp = msg.created_at.astimezone(ET).strftime("%H:%M")
            line = f"[{timestamp}] (id:{msg.id}) {msg.author.display_name}: {content}"
            total_chars += len(line)
            if total_chars > max_total:
                lines.append("... (truncated)")
                break
            lines.append(line)

        return f"Recent messages in #{channel.name}:\n" + "\n".join(lines)

    def _has_filters(self, tool_input: dict) -> bool:
        """Check if any filter parameters are present."""
        filter_keys = ("author", "contains", "before", "after", "has_attachments", "exclude_bots")
        return any(tool_input.get(k) for k in filter_keys)

    def _matches_filters(self, msg, author_member, tool_input: dict) -> bool:
        """Check if a message matches all active filters (AND logic)."""
        if author_member and msg.author.id != author_member.id:
            return False
        if tool_input.get("contains"):
            if tool_input["contains"].lower() not in (msg.content or "").lower():
                return False
        if tool_input.get("has_attachments") and not msg.attachments:
            return False
        if tool_input.get("exclude_bots") and msg.author.bot:
            return False
        return True

    def _format_history_output(self, channel_name: str, messages: list, tool_input: dict, scanned: int) -> str:
        """Format matched messages for output."""
        messages = list(reversed(messages))  # chronological order

        # Decide timestamp format: include date if time span > 24h or time filters used
        use_date = bool(tool_input.get("before") or tool_input.get("after"))
        if len(messages) >= 2:
            span = abs((messages[-1].created_at - messages[0].created_at).total_seconds())
            if span > 86400:
                use_date = True

        lines = []
        total_chars = 0
        max_total = 4000
        for msg in messages:
            content = format_message_text(msg, max_length=200, include_attachment_names=True)

            if use_date:
                timestamp = msg.created_at.astimezone(ET).strftime("%Y-%m-%d %H:%M")
            else:
                timestamp = msg.created_at.astimezone(ET).strftime("%H:%M")

            line = f"[{timestamp}] (id:{msg.id}) {msg.author.display_name}: {content}"
            total_chars += len(line)
            if total_chars > max_total:
                lines.append("... (truncated)")
                break
            lines.append(line)

        filters = _describe_active_filters(tool_input)
        header = f"Messages in #{channel_name}"
        if filters:
            header += f" ({filters})"
        header += f" — {len(messages)} match{'es' if len(messages) != 1 else ''}, {scanned} scanned:"

        return header + "\n" + "\n".join(lines)

    async def _delete_messages(self, tool_input: dict) -> str:
        channel_name = tool_input.get("channel_name")
        if not channel_name:
            return "Error: channel_name is required."

        channel = self._fuzzy_find_channel(channel_name)
        if isinstance(channel, str):
            return channel  # error message

        # Merge message_id + message_ids into a deduplicated list
        ids: list[str] = []
        if tool_input.get("message_id"):
            ids.append(tool_input["message_id"])
        if tool_input.get("message_ids"):
            ids.extend(tool_input["message_ids"])
        # Deduplicate while preserving order
        seen = set()
        unique_ids = []
        for mid in ids:
            if mid not in seen:
                seen.add(mid)
                unique_ids.append(mid)
        unique_ids = unique_ids[:10]  # cap at 10

        if unique_ids:
            # Delete specific messages by ID (single or batch)
            results: list[tuple[bool, str]] = []
            for mid in unique_ids:
                if self.active_message_id and int(mid) == self.active_message_id:
                    results.append((False, f"{mid}: can't delete my current response message"))
                    continue

                try:
                    msg = await channel.fetch_message(int(mid))
                except discord.NotFound:
                    results.append((False, f"{mid}: not found in #{channel.name}"))
                    continue
                except (ValueError, TypeError):
                    results.append((False, f"{mid}: invalid message ID"))
                    continue
                except discord.Forbidden:
                    results.append((False, f"{mid}: no permission to read #{channel.name}"))
                    continue

                if msg.author.id == self.guild.me.id:
                    await msg.delete()
                    results.append((True, f"Deleted my message in #{channel.name}"))
                else:
                    perms = channel.permissions_for(self.guild.me)
                    if not perms.manage_messages:
                        results.append((False, f"{mid}: no permission to delete others' messages in #{channel.name}"))
                        continue
                    await msg.delete()
                    results.append((True, f"Deleted a message by {msg.author.display_name} in #{channel.name}"))

            return _format_batch_results(results, "Deleted")
        else:
            # Delete the bot's own recent messages by count
            count = min(tool_input.get("count", 1), 5)
            if count < 1:
                return "Error: count must be at least 1."

            try:
                messages = await channel.history(limit=50).flatten()
            except discord.Forbidden:
                return f"I don't have permission to read #{channel.name}."

            own_msgs = [m for m in messages if m.author.id == self.guild.me.id and m.id != self.active_message_id]
            to_delete = own_msgs[:count]

            if not to_delete:
                return f"No recent messages from me found in #{channel.name}."

            for msg in to_delete:
                await msg.delete()

            return f"Deleted {len(to_delete)} of my message{'s' if len(to_delete) != 1 else ''} in #{channel.name}."

    async def _timeout_member(self, tool_input: dict) -> str:
        member_name = tool_input.get("member")
        if not member_name:
            return "Error: member is required."

        duration_str = tool_input.get("duration")
        if not duration_str:
            return "Error: duration is required."

        # Find the member
        member = attempt_to_find_member(member_name, self.guild)
        if member is None:
            return f"Could not find a member matching '{member_name}' in this server."

        # Parse the duration
        duration = _parse_duration(duration_str)
        if duration is None:
            return f"Could not parse duration: '{duration_str}'. Try '5 minutes', '1 hour', or '1 day'."

        # Guard: can't timeout bots
        if member.bot:
            return f"{member.display_name} is a bot and cannot be timed out."

        # Guard: can't timeout yourself
        if member.id == self.guild.me.id:
            return "I can't timeout myself."

        # Guard: role hierarchy — bot's top role must be higher than the target's
        if self.guild.me.top_role <= member.top_role:
            return f"I can't timeout {member.display_name} because their role is equal to or higher than mine."

        reason = tool_input.get("reason")
        try:
            await member.timeout_for(duration=duration, reason=reason)
        except discord.Forbidden:
            return f"I don't have permission to timeout {member.display_name}."

        duration_desc = duration_str.strip()
        reason_desc = f" Reason: {reason}" if reason else ""
        return f"Timed out {member.display_name} for {duration_desc}.{reason_desc}"

    async def _schedule_message(self, tool_input: dict) -> str:
        channel_name = tool_input.get("channel_name")
        if not channel_name:
            return "Error: channel_name is required."

        time_str = tool_input.get("time")
        if not time_str:
            return "Error: time is required."

        task_type = tool_input.get("type")
        if task_type not in ("static", "dynamic"):
            return "Error: type must be 'static' or 'dynamic'."

        content = tool_input.get("content")
        if not content:
            return "Error: content is required."

        reason = tool_input.get("reason")
        if not reason:
            return "Error: reason is required."

        # Resolve channel to get its ID
        channel = self._fuzzy_find_channel(channel_name)
        if isinstance(channel, str):
            return channel  # error message

        scheduler = getattr(self.bot, "scheduler", None)
        if scheduler is None:
            return "Scheduling is not available."

        return await scheduler.add_task(
            guild_id=self.guild.id,
            channel_id=channel.id,
            channel_name=channel.name,
            execute_at_str=time_str,
            task_type=task_type,
            content=content,
            reason=reason,
            created_by=self.requesting_user,
        )

    async def _manage_scheduled(self, tool_input: dict) -> str:
        action = tool_input.get("action")
        if action not in ("list", "cancel"):
            return "Error: action must be 'list' or 'cancel'."

        scheduler = getattr(self.bot, "scheduler", None)
        if scheduler is None:
            return "Scheduling is not available."

        if action == "list":
            return scheduler.list_tasks(self.guild.id)
        elif action == "cancel":
            task_id = tool_input.get("task_id")
            if not task_id:
                return "Error: task_id is required for cancel."
            return await scheduler.cancel_task(self.guild.id, task_id)

    async def _react_to_message(self, tool_input: dict) -> str:
        default_channel = tool_input.get("channel_name")
        if not default_channel:
            return "Error: channel_name is required."

        # Normalize single vs batch into a list of reaction dicts
        reactions_input = tool_input.get("reactions")
        if reactions_input:
            items = reactions_input[:20]  # cap at 20
        else:
            message_id = tool_input.get("message_id")
            if not message_id:
                return "Error: message_id is required."
            emoji = tool_input.get("emoji")
            if not emoji:
                return "Error: emoji is required."
            items = [{"channel_name": default_channel, "message_id": message_id, "emoji": emoji}]

        # Cache channel resolution per unique name
        channel_cache: dict[str, object] = {}
        results: list[tuple[bool, str]] = []

        for item in items:
            ch_name = item.get("channel_name") or default_channel
            emoji = item.get("emoji")
            message_id = item.get("message_id")

            if not message_id or not emoji:
                results.append((False, f"Missing message_id or emoji"))
                continue

            # Resolve channel (cached)
            if ch_name not in channel_cache:
                channel_cache[ch_name] = self._fuzzy_find_channel(ch_name)
            channel = channel_cache[ch_name]

            if isinstance(channel, str):
                results.append((False, f"{emoji} on {message_id}: {channel}"))
                continue

            try:
                msg = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                results.append((False, f"{emoji} on {message_id}: message not found in #{channel.name}"))
                continue
            except (ValueError, TypeError):
                results.append((False, f"{emoji} on {message_id}: invalid message ID"))
                continue
            except discord.Forbidden:
                results.append((False, f"{emoji} on {message_id}: no permission to read #{channel.name}"))
                continue

            try:
                await msg.add_reaction(emoji)
            except discord.Forbidden:
                results.append((False, f"{emoji} on {message_id}: no permission to add reactions in #{channel.name}"))
                continue
            except discord.NotFound:
                results.append((False, f"{emoji} on {message_id}: emoji '{emoji}' not found"))
                continue
            except discord.HTTPException as e:
                results.append((False, f"{emoji} on {message_id}: {e}"))
                continue

            results.append((True, f"Reacted with {emoji} to a message in #{channel.name}"))

        return _format_batch_results(results, "Reacted")

    async def _save_memory(self, tool_input: dict) -> str:
        if self.memory_store is None:
            return "Memory is not enabled."
        key = tool_input.get("key", "")
        content = tool_input.get("content", "")
        return await self.memory_store.save_memory(self.guild.id, key, content)

    async def _delete_memory(self, tool_input: dict) -> str:
        if self.memory_store is None:
            return "Memory is not enabled."
        key = tool_input.get("key", "")
        return await self.memory_store.delete_memory(self.guild.id, key)

    def _no_results_message(self, channel_name: str, tool_input: dict, scanned: int) -> str:
        """Informative message when no messages match the filters."""
        filters = _describe_active_filters(tool_input)
        msg = f"No messages found in #{channel_name}"
        if filters:
            msg += f" matching: {filters}"
        msg += f" (scanned {scanned} messages)."
        return msg

    @staticmethod
    def _normalize_name(s: str) -> str:
        """Normalize a channel name for fuzzy comparison.

        Replaces Unicode quote variants with ASCII equivalents so that
        e.g. \u2019 (right single quote) matches a plain apostrophe.
        """
        # Single-quote variants -> ASCII apostrophe
        s = s.replace("\u2018", "'")  # left single quote
        s = s.replace("\u2019", "'")  # right single quote
        s = s.replace("\u02bc", "'")  # modifier letter apostrophe
        s = s.replace("\u2032", "'")  # prime
        # Double-quote variants -> ASCII double quote
        s = s.replace("\u201c", '"')  # left double quote
        s = s.replace("\u201d", '"')  # right double quote
        return s

    def _fuzzy_find_channel(self, name: str, channel_types: list[str] = None):
        """Find a channel by name with fuzzy matching. Returns channel or error string."""
        # Normalize: strip leading #
        name = name.lstrip("#")

        logger.debug(f"Fuzzy find channel: query={name!r}")

        candidates = list(self.guild.channels) + list(self.guild.threads)

        # Filter by type if requested
        if channel_types:
            type_map = {
                "text": discord.TextChannel,
                "voice": discord.VoiceChannel,
                "stage_voice": discord.StageChannel,
                "forum": discord.ForumChannel,
            }
            allowed = tuple(type_map[t] for t in channel_types if t in type_map)
            candidates = [ch for ch in candidates if isinstance(ch, allowed)]

        logger.debug(f"Fuzzy find channel: {len(candidates)} candidates")

        # Normalize the query for comparison
        normalized_name = self._normalize_name(name)

        # Exact match (raw then normalized)
        for ch in candidates:
            if ch.name == name:
                logger.debug(f"Fuzzy find channel: exact match -> #{ch.name}")
                return ch
            if self._normalize_name(ch.name) == normalized_name:
                logger.debug(f"Fuzzy find channel: normalized exact match -> #{ch.name}")
                return ch

        # Case-insensitive match (normalized)
        lower_name = normalized_name.lower()
        for ch in candidates:
            if self._normalize_name(ch.name).lower() == lower_name:
                logger.debug(f"Fuzzy find channel: case-insensitive match -> #{ch.name}")
                return ch

        # Substring match (normalized)
        for ch in candidates:
            if lower_name in self._normalize_name(ch.name).lower():
                logger.debug(f"Fuzzy find channel: substring match -> #{ch.name}")
                return ch

        # No match found — build helpful error
        available = sorted(set(ch.name for ch in candidates))[:20]
        available_str = ", ".join(f"#{n}" for n in available)
        logger.warning(f"Fuzzy find channel: no match for {name!r}. Available: {available_str}")
        return f"Could not find a channel matching '{name}'. Available channels: {available_str}"
