import discord
from loguru import logger

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
        "description": "Read recent messages from a channel or thread in the server.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Name of the channel or thread to read from."
                },
                "num_messages": {
                    "type": "integer",
                    "description": "Number of recent messages to fetch (default 25, max 50)."
                }
            },
            "required": ["channel_name"]
        }
    }
]


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
        return f"Reading history from #{channel}..."
    return "Using a tool..."


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
            content = msg.content or "[no text]"
            if len(content) > 200:
                content = content[:200] + "..."
            timestamp = msg.created_at.strftime("%H:%M")
            line = f"[{timestamp}] {msg.author.display_name}: {content}"
            total_chars += len(line)
            if total_chars > max_total:
                lines.append("... (truncated)")
                break
            lines.append(line)

        return f"Recent messages in #{channel.name}:\n" + "\n".join(lines)

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
