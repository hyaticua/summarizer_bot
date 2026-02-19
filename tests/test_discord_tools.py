"""
Unit tests for Discord tool execution (discord_tools.py).

Tests cover:
- _status_for_tool(): user-facing status strings
- _fuzzy_find_channel(): channel name resolution
- _format_channel(): channel display formatting
- DiscordToolExecutor: get_server_members, list_channels, read_channel_history, dispatch
"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from datetime import datetime

import discord

from summarizer_bot.discord_tools import (
    _status_for_tool,
    DiscordToolExecutor,
)


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------


def make_member(id, name, bot=False, voice_channel=None):
    """Create a mock discord.Member."""
    m = Mock(spec=discord.Member)
    m.id = id
    m.display_name = name
    m.bot = bot
    if voice_channel:
        m.voice = Mock()
        m.voice.channel = voice_channel
    else:
        m.voice = None
    return m


def make_text_channel(name, messages=None, can_read=True):
    """Create a mock discord.TextChannel with history support."""
    ch = Mock(spec=discord.TextChannel)
    ch.name = name
    ch.category = None
    ch.members = []

    msgs = messages or []
    ch.history.return_value.flatten = AsyncMock(return_value=list(msgs))

    perms = Mock()
    perms.read_message_history = can_read
    ch.permissions_for = Mock(return_value=perms)

    return ch


def make_voice_channel(name, members=None):
    """Create a mock discord.VoiceChannel."""
    ch = Mock(spec=discord.VoiceChannel)
    ch.name = name
    ch.members = members or []
    ch.category = None
    return ch


def make_stage_channel(name, members=None):
    """Create a mock discord.StageChannel."""
    ch = Mock(spec=discord.StageChannel)
    ch.name = name
    ch.members = members or []
    ch.category = None
    return ch


def make_forum_channel(name):
    """Create a mock discord.ForumChannel."""
    ch = Mock(spec=discord.ForumChannel)
    ch.name = name
    ch.category = None
    return ch


def make_category(name, channels):
    """Create a mock discord.CategoryChannel and link its children."""
    cat = Mock(spec=discord.CategoryChannel)
    cat.name = name
    cat.channels = channels
    for ch in channels:
        ch.category = cat
    return cat


def make_guild(members, channels, categories=None, threads=None):
    """Assemble a mock discord.Guild."""
    g = Mock(spec=discord.Guild)
    g.members = members
    g.member_count = len(members)
    g.channels = channels
    g.categories = categories or []
    g.threads = threads or []
    g.voice_channels = [
        ch for ch in channels if isinstance(ch, discord.VoiceChannel)
    ]
    g.stage_channels = [
        ch for ch in channels if isinstance(ch, discord.StageChannel)
    ]
    g.me = make_member(0, "TestBot", bot=True)
    return g


def make_message(author, content, timestamp=None):
    """Create a mock discord.Message."""
    msg = Mock(spec=discord.Message)
    msg.author = author
    msg.content = content
    msg.created_at = timestamp or datetime(2025, 1, 1, 12, 0)
    return msg


def _executor(guild):
    """Create a DiscordToolExecutor for the given guild."""
    bot = Mock(spec=discord.Bot)
    return DiscordToolExecutor(guild, bot)


# ---------------------------------------------------------------------------
# Tests: _status_for_tool
# ---------------------------------------------------------------------------


class TestStatusForTool:
    def test_members_with_channel(self):
        result = _status_for_tool(
            "get_server_members", {"filter": "channel", "channel_name": "general"}
        )
        assert result == "Checking who's in #general..."

    def test_members_voice(self):
        result = _status_for_tool("get_server_members", {"filter": "voice"})
        assert result == "Checking voice channels..."

    def test_members_all(self):
        result = _status_for_tool("get_server_members", {"filter": "all"})
        assert result == "Checking server members..."

    def test_list_channels(self):
        assert _status_for_tool("list_channels", {}) == "Listing server channels..."

    def test_read_channel_history(self):
        result = _status_for_tool("read_channel_history", {"channel_name": "dev"})
        assert result == "Reading history from #dev..."

    def test_unknown_tool(self):
        assert _status_for_tool("something_else", {}) == "Using a tool..."


# ---------------------------------------------------------------------------
# Tests: _fuzzy_find_channel
# ---------------------------------------------------------------------------


class TestFuzzyFindChannel:
    def _make_executor(self, channels, threads=None):
        guild = make_guild([], channels, threads=threads)
        return _executor(guild)

    def test_exact_match(self):
        ch = make_text_channel("general")
        ex = self._make_executor([ch])
        assert ex._fuzzy_find_channel("general") is ch

    def test_case_insensitive(self):
        ch = make_text_channel("General")
        ex = self._make_executor([ch])
        assert ex._fuzzy_find_channel("general") is ch

    def test_substring(self):
        ch = make_text_channel("dev-backend")
        ex = self._make_executor([ch])
        assert ex._fuzzy_find_channel("backend") is ch

    def test_hash_stripping(self):
        ch = make_text_channel("general")
        ex = self._make_executor([ch])
        assert ex._fuzzy_find_channel("#general") is ch

    def test_type_filtering(self):
        text_ch = make_text_channel("music")
        voice_ch = make_voice_channel("music")
        ex = self._make_executor([text_ch, voice_ch])
        result = ex._fuzzy_find_channel("music", channel_types=["voice"])
        assert result is voice_ch

    def test_not_found_returns_error_with_available(self):
        ch = make_text_channel("general")
        ex = self._make_executor([ch])
        result = ex._fuzzy_find_channel("nonexistent")
        assert isinstance(result, str)
        assert "Could not find" in result
        assert "#general" in result


# ---------------------------------------------------------------------------
# Tests: _format_channel
# ---------------------------------------------------------------------------


class TestFormatChannel:
    def _executor(self):
        guild = make_guild([], [])
        return _executor(guild)

    def test_text_channel(self):
        ch = make_text_channel("general")
        assert self._executor()._format_channel(ch) == "  - #general (text)"

    def test_voice_channel_empty(self):
        ch = make_voice_channel("gaming", members=[])
        assert self._executor()._format_channel(ch) == "  - #gaming (voice)"

    def test_voice_channel_occupied(self):
        ex = self._executor()
        # Singular
        ch1 = make_voice_channel("gaming", members=[make_member(1, "Alice")])
        result1 = ex._format_channel(ch1)
        assert result1 == "  - #gaming (voice — 1 member)"

        # Plural
        ch2 = make_voice_channel(
            "gaming", members=[make_member(1, "Alice"), make_member(2, "Bob")]
        )
        result2 = ex._format_channel(ch2)
        assert result2 == "  - #gaming (voice — 2 members)"

    def test_stage_and_forum(self):
        ex = self._executor()
        stage = make_stage_channel("events")
        assert ex._format_channel(stage) == "  - #events (stage)"

        forum = make_forum_channel("help")
        assert ex._format_channel(forum) == "  - #help (forum)"


# ---------------------------------------------------------------------------
# Tests: get_server_members
# ---------------------------------------------------------------------------


class TestGetServerMembers:
    @pytest.mark.asyncio
    async def test_all_basic(self):
        members = [make_member(1, "Alice"), make_member(2, "Bob")]
        guild = make_guild(members, [])
        ex = _executor(guild)
        result = await ex.execute("get_server_members", {"filter": "all"})
        assert "Alice" in result
        assert "Bob" in result
        assert "2 shown" in result

    @pytest.mark.asyncio
    async def test_all_annotations(self):
        vc = make_voice_channel("Gaming")
        bot = make_member(1, "MusicBot", bot=True)
        user_in_voice = make_member(2, "Alice", voice_channel=vc)
        guild = make_guild([bot, user_in_voice], [vc])
        ex = _executor(guild)
        result = await ex.execute("get_server_members", {"filter": "all"})
        assert "bot" in result
        assert "in voice: #Gaming" in result

    @pytest.mark.asyncio
    async def test_voice_all_channels(self):
        alice = make_member(1, "Alice")
        vc = make_voice_channel("Gaming", members=[alice])
        guild = make_guild([alice], [vc])
        ex = _executor(guild)
        result = await ex.execute("get_server_members", {"filter": "voice"})
        assert "#Gaming: Alice" in result

    @pytest.mark.asyncio
    async def test_voice_specific_channel(self):
        alice = make_member(1, "Alice")
        vc = make_voice_channel("Gaming", members=[alice])
        guild = make_guild([alice], [vc])
        ex = _executor(guild)
        result = await ex.execute(
            "get_server_members", {"filter": "voice", "channel_name": "Gaming"}
        )
        assert "Members in #Gaming" in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_voice_nobody(self):
        vc = make_voice_channel("Gaming", members=[])
        guild = make_guild([], [vc])
        ex = _executor(guild)
        result = await ex.execute("get_server_members", {"filter": "voice"})
        assert "No one" in result

    @pytest.mark.asyncio
    async def test_channel_active_authors(self):
        alice = make_member(1, "Alice", bot=False)
        bot = make_member(2, "BotUser", bot=True)
        msgs = [make_message(alice, "hi"), make_message(bot, "beep")]
        ch = make_text_channel("general", messages=msgs)
        guild = make_guild([alice, bot], [ch])
        ex = _executor(guild)
        result = await ex.execute(
            "get_server_members", {"filter": "channel", "channel_name": "general"}
        )
        assert "Alice" in result
        assert "BotUser" not in result

    @pytest.mark.asyncio
    async def test_channel_missing_name(self):
        guild = make_guild([], [])
        ex = _executor(guild)
        result = await ex.execute("get_server_members", {"filter": "channel"})
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_channel_forbidden(self):
        ch = make_text_channel("secret")
        ch.history.return_value.flatten = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "")
        )
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute(
            "get_server_members", {"filter": "channel", "channel_name": "secret"}
        )
        assert "permission" in result.lower()


# ---------------------------------------------------------------------------
# Tests: list_channels
# ---------------------------------------------------------------------------


class TestListChannels:
    @pytest.mark.asyncio
    async def test_categorized_and_uncategorized(self):
        text = make_text_channel("general")
        cat = make_category("Text Channels", [text])
        loose = make_text_channel("random")  # uncategorized
        guild = make_guild([], [text, loose], categories=[cat])
        ex = _executor(guild)
        result = await ex.execute("list_channels", {})
        assert "Text Channels" in result
        assert "#general" in result
        assert "Uncategorized" in result
        assert "#random" in result

    @pytest.mark.asyncio
    async def test_voice_occupancy_shown(self):
        alice = make_member(1, "Alice")
        vc = make_voice_channel("Gaming", members=[alice])
        cat = make_category("Voice", [vc])
        guild = make_guild([alice], [vc], categories=[cat])
        ex = _executor(guild)
        result = await ex.execute("list_channels", {})
        assert "1 member" in result

    @pytest.mark.asyncio
    async def test_include_threads(self):
        ch = make_text_channel("general")
        thread = Mock(spec=discord.Thread)
        thread.name = "my-thread"
        thread.parent = ch
        thread.category = None
        guild = make_guild([], [ch], threads=[thread])
        ex = _executor(guild)
        result = await ex.execute("list_channels", {"include_threads": True})
        assert "my-thread" in result
        assert "Active Threads" in result


# ---------------------------------------------------------------------------
# Tests: read_channel_history
# ---------------------------------------------------------------------------


class TestReadChannelHistory:
    @pytest.mark.asyncio
    async def test_basic_read(self):
        alice = make_member(1, "Alice")
        msg = make_message(alice, "Hello world", datetime(2025, 1, 1, 14, 30))
        ch = make_text_channel("general", messages=[msg])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {"channel_name": "general"})
        assert "Alice" in result
        assert "Hello world" in result
        assert "14:30" in result

    @pytest.mark.asyncio
    async def test_num_messages_capped_at_50(self):
        ch = make_text_channel("general", messages=[])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        await ex.execute(
            "read_channel_history", {"channel_name": "general", "num_messages": 100}
        )
        ch.history.assert_called_with(limit=50)

    @pytest.mark.asyncio
    async def test_long_message_truncated(self):
        alice = make_member(1, "Alice")
        long_content = "x" * 300
        msg = make_message(alice, long_content)
        ch = make_text_channel("general", messages=[msg])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {"channel_name": "general"})
        assert "..." in result
        assert "x" * 201 not in result

    @pytest.mark.asyncio
    async def test_total_output_truncated(self):
        alice = make_member(1, "Alice")
        msgs = [
            make_message(alice, "a" * 190, datetime(2025, 1, 1, 12, i % 60))
            for i in range(50)
        ]
        ch = make_text_channel("general", messages=msgs)
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {"channel_name": "general"})
        assert "truncated" in result.lower()

    @pytest.mark.asyncio
    async def test_no_permission(self):
        ch = make_text_channel("secret", can_read=False)
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute(
            "read_channel_history", {"channel_name": "secret"}
        )
        assert "permission" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_channel(self):
        ch = make_text_channel("general", messages=[])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {"channel_name": "general"})
        assert "No recent messages" in result


# ---------------------------------------------------------------------------
# Tests: execute dispatch
# ---------------------------------------------------------------------------


class TestExecuteDispatch:
    @pytest.mark.asyncio
    async def test_routes_to_correct_handler(self):
        """All three tool names dispatch without error."""
        ch = make_text_channel("general", messages=[])
        guild = make_guild([make_member(1, "Alice")], [ch])
        ex = _executor(guild)

        r1 = await ex.execute("get_server_members", {"filter": "all"})
        assert "Unknown tool" not in r1 and "Error" not in r1

        r2 = await ex.execute("list_channels", {})
        assert "Unknown tool" not in r2 and "Error" not in r2

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        guild = make_guild([], [])
        ex = _executor(guild)
        result = await ex.execute("nonexistent_tool", {})
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        guild = make_guild([], [])
        ex = _executor(guild)
        ex._get_server_members = AsyncMock(side_effect=RuntimeError("boom"))
        result = await ex.execute("get_server_members", {"filter": "all"})
        assert "Error" in result
        assert "boom" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
