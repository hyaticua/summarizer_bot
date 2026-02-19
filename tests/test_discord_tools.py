"""
Unit tests for Discord tool execution (discord_tools.py).

Tests cover:
- _status_for_tool(): user-facing status strings
- _fuzzy_find_channel(): channel name resolution
- _format_channel(): channel display formatting
- DiscordToolExecutor: get_server_members, list_channels, read_channel_history, dispatch
- _parse_time_expression(): time expression parsing
- _matches_filters(): message filter logic
- Filtered read_channel_history integration
"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

import discord

from summarizer_bot.discord_tools import (
    _status_for_tool,
    _parse_time_expression,
    _describe_active_filters,
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


def make_attachment(filename="file.png"):
    """Create a mock discord.Attachment."""
    att = Mock(spec=discord.Attachment)
    att.filename = filename
    return att


def make_message(author, content, timestamp=None, attachments=None, reference=None):
    """Create a mock discord.Message."""
    msg = Mock(spec=discord.Message)
    msg.author = author
    msg.content = content
    msg.created_at = timestamp or datetime(2025, 1, 1, 12, 0)
    msg.attachments = attachments or []
    msg.reference = reference
    msg.guild = Mock(spec=discord.Guild)
    msg.guild.get_member = Mock(return_value=None)
    msg.guild.get_channel = Mock(return_value=None)
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
        assert result == "Reading messages from #dev..."

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


# ---------------------------------------------------------------------------
# Tests: _parse_time_expression
# ---------------------------------------------------------------------------


class TestParseTimeExpression:
    def test_yesterday(self):
        result = _parse_time_expression("yesterday")
        assert result is not None
        now = datetime.now(timezone.utc)
        expected = now - timedelta(days=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_today(self):
        result = _parse_time_expression("today")
        assert result is not None
        now = datetime.now(timezone.utc)
        assert result.hour == 0 and result.minute == 0
        assert result.date() == now.date()

    def test_last_week(self):
        result = _parse_time_expression("last week")
        assert result is not None
        now = datetime.now(timezone.utc)
        expected = now - timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_last_month(self):
        result = _parse_time_expression("last month")
        assert result is not None
        now = datetime.now(timezone.utc)
        expected = now - timedelta(days=30)
        assert abs((result - expected).total_seconds()) < 2

    def test_n_hours_ago(self):
        result = _parse_time_expression("2 hours ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        expected = now - timedelta(hours=2)
        assert abs((result - expected).total_seconds()) < 2

    def test_n_days_ago(self):
        result = _parse_time_expression("3 days ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        expected = now - timedelta(days=3)
        assert abs((result - expected).total_seconds()) < 2

    def test_n_minutes_ago(self):
        result = _parse_time_expression("30 minutes ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        expected = now - timedelta(minutes=30)
        assert abs((result - expected).total_seconds()) < 2

    def test_singular_unit(self):
        result = _parse_time_expression("1 hour ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        expected = now - timedelta(hours=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_absolute_date(self):
        result = _parse_time_expression("2024-01-15")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.tzinfo is not None

    def test_absolute_date_with_time(self):
        result = _parse_time_expression("2024-06-15 14:30")
        assert result is not None
        assert result.year == 2024
        assert result.hour == 14
        assert result.minute == 30

    def test_invalid_returns_none(self):
        assert _parse_time_expression("not a time") is None
        assert _parse_time_expression("") is None
        assert _parse_time_expression("xyz ago") is None

    def test_whitespace_stripped(self):
        result = _parse_time_expression("  yesterday  ")
        assert result is not None


# ---------------------------------------------------------------------------
# Tests: _describe_active_filters
# ---------------------------------------------------------------------------


class TestDescribeActiveFilters:
    def test_no_filters(self):
        assert _describe_active_filters({}) == ""

    def test_author_only(self):
        result = _describe_active_filters({"author": "Alice"})
        assert "Alice" in result

    def test_contains_only(self):
        result = _describe_active_filters({"contains": "bug"})
        assert "bug" in result

    def test_time_filters(self):
        result = _describe_active_filters({"after": "yesterday", "before": "today"})
        assert "after yesterday" in result
        assert "before today" in result

    def test_combined(self):
        result = _describe_active_filters({
            "author": "Alice",
            "contains": "bug",
            "has_attachments": True,
        })
        assert "Alice" in result
        assert "bug" in result
        assert "attachments" in result


# ---------------------------------------------------------------------------
# Tests: _matches_filters
# ---------------------------------------------------------------------------


class TestMatchesFilters:
    def _make_executor(self):
        guild = make_guild([], [])
        return _executor(guild)

    def test_no_filters_matches_all(self):
        ex = self._make_executor()
        alice = make_member(1, "Alice")
        msg = make_message(alice, "hello")
        assert ex._matches_filters(msg, None, {}) is True

    def test_author_filter_match(self):
        ex = self._make_executor()
        alice = make_member(1, "Alice")
        bob = make_member(2, "Bob")
        msg = make_message(alice, "hello")
        assert ex._matches_filters(msg, alice, {"author": "Alice"}) is True
        assert ex._matches_filters(msg, bob, {"author": "Bob"}) is False

    def test_contains_filter(self):
        ex = self._make_executor()
        alice = make_member(1, "Alice")
        msg = make_message(alice, "This is a bug report")
        assert ex._matches_filters(msg, None, {"contains": "bug"}) is True
        assert ex._matches_filters(msg, None, {"contains": "BUG"}) is True  # case-insensitive
        assert ex._matches_filters(msg, None, {"contains": "feature"}) is False

    def test_contains_filter_no_content(self):
        ex = self._make_executor()
        alice = make_member(1, "Alice")
        msg = make_message(alice, None)
        assert ex._matches_filters(msg, None, {"contains": "bug"}) is False

    def test_has_attachments_filter(self):
        ex = self._make_executor()
        alice = make_member(1, "Alice")
        msg_with = make_message(alice, "check this", attachments=[make_attachment()])
        msg_without = make_message(alice, "no file")
        assert ex._matches_filters(msg_with, None, {"has_attachments": True}) is True
        assert ex._matches_filters(msg_without, None, {"has_attachments": True}) is False

    def test_exclude_bots_filter(self):
        ex = self._make_executor()
        human = make_member(1, "Alice", bot=False)
        bot = make_member(2, "BotUser", bot=True)
        msg_human = make_message(human, "hello")
        msg_bot = make_message(bot, "beep")
        assert ex._matches_filters(msg_human, None, {"exclude_bots": True}) is True
        assert ex._matches_filters(msg_bot, None, {"exclude_bots": True}) is False

    def test_combined_and_logic(self):
        ex = self._make_executor()
        alice = make_member(1, "Alice", bot=False)
        msg = make_message(alice, "found a bug", attachments=[make_attachment()])
        # All filters match
        assert ex._matches_filters(msg, alice, {
            "author": "Alice",
            "contains": "bug",
            "has_attachments": True,
            "exclude_bots": True,
        }) is True
        # One filter fails (wrong author)
        bob = make_member(2, "Bob")
        assert ex._matches_filters(msg, bob, {
            "author": "Bob",
            "contains": "bug",
        }) is False


# ---------------------------------------------------------------------------
# Tests: _status_for_tool with filters
# ---------------------------------------------------------------------------


class TestStatusForToolFilters:
    def test_no_filters(self):
        result = _status_for_tool("read_channel_history", {"channel_name": "general"})
        assert result == "Reading messages from #general..."

    def test_with_author_filter(self):
        result = _status_for_tool("read_channel_history", {
            "channel_name": "general",
            "author": "Alice",
        })
        assert "Searching" in result
        assert "Alice" in result

    def test_with_contains_filter(self):
        result = _status_for_tool("read_channel_history", {
            "channel_name": "general",
            "contains": "bug report",
        })
        assert "Searching" in result
        assert "bug report" in result

    def test_with_multiple_filters(self):
        result = _status_for_tool("read_channel_history", {
            "channel_name": "dev",
            "author": "Alice",
            "contains": "error",
        })
        assert "Searching #dev" in result
        assert "Alice" in result
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests: Filtered read_channel_history (integration)
# ---------------------------------------------------------------------------


def _make_filtered_channel(name, messages):
    """Create a mock channel whose history() supports keyword args for filtering."""
    ch = Mock(spec=discord.TextChannel)
    ch.name = name
    ch.category = None
    ch.members = []

    perms = Mock()
    perms.read_message_history = True
    ch.permissions_for = Mock(return_value=perms)

    # The history mock needs to return batches based on kwargs
    # For simplicity, return all messages and let the test verify filtering
    def history_side_effect(**kwargs):
        limit = kwargs.get("limit", 100)
        before = kwargs.get("before")
        after = kwargs.get("after")

        filtered = list(messages)
        if before:
            filtered = [m for m in filtered if m.created_at < before]
        if after:
            filtered = [m for m in filtered if m.created_at > after]
        # Discord returns newest first
        filtered.sort(key=lambda m: m.created_at, reverse=True)
        filtered = filtered[:limit]

        result = Mock()
        result.flatten = AsyncMock(return_value=filtered)
        return result

    ch.history = Mock(side_effect=history_side_effect)
    return ch


class TestFilteredReadChannelHistory:
    @pytest.mark.asyncio
    async def test_unfiltered_still_works(self):
        """No filters = same behavior as before."""
        alice = make_member(1, "Alice")
        msg = make_message(alice, "Hello", datetime(2025, 1, 1, 14, 30))
        ch = make_text_channel("general", messages=[msg])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {"channel_name": "general"})
        assert "Alice" in result
        assert "Hello" in result

    @pytest.mark.asyncio
    async def test_filter_by_author(self):
        alice = make_member(1, "Alice")
        alice.name = "alice"
        alice.nick = None
        alice.global_name = None
        bob = make_member(2, "Bob")
        bob.name = "bob"
        bob.nick = None
        bob.global_name = None

        msgs = [
            make_message(alice, "Alice msg 1", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)),
            make_message(bob, "Bob msg", datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc)),
            make_message(alice, "Alice msg 2", datetime(2025, 1, 1, 12, 2, tzinfo=timezone.utc)),
        ]
        ch = _make_filtered_channel("general", msgs)
        guild = make_guild([alice, bob], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "author": "Alice",
        })
        assert "Alice msg 1" in result
        assert "Alice msg 2" in result
        assert "Bob msg" not in result

    @pytest.mark.asyncio
    async def test_filter_by_contains(self):
        alice = make_member(1, "Alice")
        msgs = [
            make_message(alice, "This has a bug", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)),
            make_message(alice, "This is fine", datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc)),
            make_message(alice, "Another bug here", datetime(2025, 1, 1, 12, 2, tzinfo=timezone.utc)),
        ]
        ch = _make_filtered_channel("general", msgs)
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "contains": "bug",
        })
        assert "This has a bug" in result
        assert "Another bug here" in result
        assert "This is fine" not in result

    @pytest.mark.asyncio
    async def test_filter_by_has_attachments(self):
        alice = make_member(1, "Alice")
        msgs = [
            make_message(alice, "no file", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)),
            make_message(alice, "has file", datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc),
                         attachments=[make_attachment("photo.jpg")]),
        ]
        ch = _make_filtered_channel("general", msgs)
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "has_attachments": True,
        })
        assert "has file" in result
        assert "photo.jpg" in result
        assert "no file" not in result

    @pytest.mark.asyncio
    async def test_filter_by_exclude_bots(self):
        human = make_member(1, "Alice", bot=False)
        bot = make_member(2, "BotUser", bot=True)
        msgs = [
            make_message(human, "human msg", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)),
            make_message(bot, "bot msg", datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc)),
        ]
        ch = _make_filtered_channel("general", msgs)
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "exclude_bots": True,
        })
        assert "human msg" in result
        assert "bot msg" not in result

    @pytest.mark.asyncio
    async def test_no_matches_returns_informative_message(self):
        alice = make_member(1, "Alice")
        msgs = [
            make_message(alice, "hello", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)),
        ]
        ch = _make_filtered_channel("general", msgs)
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "contains": "nonexistent keyword xyz",
        })
        assert "No messages found" in result
        assert "scanned" in result

    @pytest.mark.asyncio
    async def test_unknown_author_returns_error(self):
        ch = _make_filtered_channel("general", [])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "author": "NonExistentUser",
        })
        assert "Could not find a member" in result

    @pytest.mark.asyncio
    async def test_invalid_time_expression(self):
        ch = _make_filtered_channel("general", [])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "after": "not a real time",
        })
        assert "Could not parse" in result

    @pytest.mark.asyncio
    async def test_after_before_ordering_error(self):
        ch = _make_filtered_channel("general", [])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "after": "2025-06-01",
            "before": "2025-01-01",
        })
        assert "after" in result.lower() and "earlier" in result.lower()

    @pytest.mark.asyncio
    async def test_filter_with_time_after(self):
        alice = make_member(1, "Alice")
        old = make_message(alice, "old msg", datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))
        new = make_message(alice, "new msg", datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc))
        ch = _make_filtered_channel("general", [old, new])
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "after": "2025-01-01",
        })
        assert "new msg" in result
        assert "old msg" not in result

    @pytest.mark.asyncio
    async def test_combined_filters(self):
        alice = make_member(1, "Alice", bot=False)
        alice.name = "alice"
        alice.nick = None
        alice.global_name = None
        bob = make_member(2, "Bob", bot=False)
        bob.name = "bob"
        bob.nick = None
        bob.global_name = None

        msgs = [
            make_message(alice, "Alice bug report", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)),
            make_message(bob, "Bob bug report", datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc)),
            make_message(alice, "Alice feature request", datetime(2025, 1, 1, 12, 2, tzinfo=timezone.utc)),
        ]
        ch = _make_filtered_channel("general", msgs)
        guild = make_guild([alice, bob], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "author": "Alice",
            "contains": "bug",
        })
        assert "Alice bug report" in result
        assert "Bob bug report" not in result
        assert "feature request" not in result

    @pytest.mark.asyncio
    async def test_output_header_includes_filter_info(self):
        alice = make_member(1, "Alice")
        msgs = [
            make_message(alice, "match", datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)),
        ]
        ch = _make_filtered_channel("general", msgs)
        guild = make_guild([], [ch])
        ex = _executor(guild)
        result = await ex.execute("read_channel_history", {
            "channel_name": "general",
            "contains": "match",
        })
        assert "scanned" in result
        assert "match" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
