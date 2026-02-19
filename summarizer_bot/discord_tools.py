import re
from datetime import datetime, timedelta, timezone

import discord
from dateutil import parser as dateutil_parser
from loguru import logger

try:
    from message import attempt_to_find_member, format_message_text
except ImportError:
    from .message import attempt_to_find_member, format_message_text

SCAN_LIMIT = 500   # Max messages to scan from Discord when filtering
BATCH_SIZE = 100    # Messages per Discord API call (Discord's max)

DISCORD_TOOLS = [
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
    }
]


def _parse_time_expression(expr: str) -> datetime | None:
    """Parse a human-readable time expression into a UTC datetime.

    Supports:
    - Relative: "yesterday", "today", "last week", "last month", "N units ago"
    - Absolute: anything dateutil can parse (e.g. "2024-01-15", "Jan 15 2024")

    Returns None if the expression cannot be parsed.
    """
    expr = expr.strip().lower()
    now = datetime.now(timezone.utc)

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
            parsed = parsed.replace(tzinfo=timezone.utc)
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


class DiscordToolExecutor:
    def __init__(self, guild: discord.Guild, bot: discord.Bot):
        self.guild = guild
        self.bot = bot

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
            timestamp = msg.created_at.strftime("%H:%M")
            line = f"[{timestamp}] {msg.author.display_name}: {content}"
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
                timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
            else:
                timestamp = msg.created_at.strftime("%H:%M")

            line = f"[{timestamp}] {msg.author.display_name}: {content}"
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
