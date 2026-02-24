"""
Unit tests for scheduler.py.

Tests cover:
- _parse_future_time(): relative and absolute time expressions
- Scheduler.add_task(): validation (limits, past time, horizon, guild cap)
- Scheduler.list_tasks(): listing tasks per guild
- Scheduler.cancel_task(): cancelling by ID
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from summarizer_bot.scheduler import (
    _parse_future_time,
    Scheduler,
    ScheduledTask,
    MAX_TASKS_PER_GUILD,
    MAX_HORIZON_DAYS,
    MIN_LEAD_SECONDS,
)


# ---------------------------------------------------------------------------
# _parse_future_time tests
# ---------------------------------------------------------------------------

class TestParseFutureTime:
    def test_in_minutes(self):
        before = datetime.now(timezone.utc)
        result = _parse_future_time("in 5 minutes")
        after = datetime.now(timezone.utc)

        assert result is not None
        assert before + timedelta(minutes=5) <= result <= after + timedelta(minutes=5)

    def test_in_hours(self):
        before = datetime.now(timezone.utc)
        result = _parse_future_time("in 2 hours")
        after = datetime.now(timezone.utc)

        assert result is not None
        assert before + timedelta(hours=2) <= result <= after + timedelta(hours=2)

    def test_in_days(self):
        before = datetime.now(timezone.utc)
        result = _parse_future_time("in 3 days")

        assert result is not None
        expected = before + timedelta(days=3)
        assert abs((result - expected).total_seconds()) < 2

    def test_in_weeks(self):
        before = datetime.now(timezone.utc)
        result = _parse_future_time("in 1 week")

        assert result is not None
        expected = before + timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_in_seconds(self):
        before = datetime.now(timezone.utc)
        result = _parse_future_time("in 30 seconds")

        assert result is not None
        expected = before + timedelta(seconds=30)
        assert abs((result - expected).total_seconds()) < 2

    def test_tomorrow_at_time(self):
        result = _parse_future_time("tomorrow at 9am")

        assert result is not None
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        assert result.day == tomorrow.day
        assert result.hour == 9
        assert result.minute == 0

    def test_today_at_time(self):
        result = _parse_future_time("today at 3:30pm")

        assert result is not None
        now = datetime.now(timezone.utc)
        assert result.day == now.day
        assert result.hour == 15
        assert result.minute == 30

    def test_absolute_date(self):
        result = _parse_future_time("2027-06-15 14:00")

        assert result is not None
        assert result.year == 2027
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 0

    def test_invalid_expression(self):
        result = _parse_future_time("gibberish not a time")
        assert result is None

    def test_whitespace_handling(self):
        result = _parse_future_time("  in 10 minutes  ")
        assert result is not None

    def test_singular_unit(self):
        before = datetime.now(timezone.utc)
        result = _parse_future_time("in 1 minute")
        assert result is not None
        expected = before + timedelta(minutes=1)
        assert abs((result - expected).total_seconds()) < 2


# ---------------------------------------------------------------------------
# Scheduler tests
# ---------------------------------------------------------------------------

def make_mock_bot():
    bot = MagicMock()
    bot.scheduler = None  # will be set by Scheduler
    return bot


class TestSchedulerAddTask:
    @pytest.fixture
    def scheduler(self, tmp_path, monkeypatch):
        # Point the tasks file to a temp location
        monkeypatch.setattr("summarizer_bot.scheduler.TASKS_FILE", str(tmp_path / "tasks.json"))
        bot = make_mock_bot()
        s = Scheduler(bot)
        return s

    @pytest.mark.asyncio
    async def test_add_valid_task(self, scheduler):
        result = await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="Hello!",
            reason="Test reminder",
            created_by="TestUser",
        )

        assert "Scheduled" in result
        assert "general" in result
        assert len(scheduler.tasks) == 1
        assert scheduler.tasks[0].task_type == "static"
        assert scheduler.tasks[0].content == "Hello!"

    @pytest.mark.asyncio
    async def test_add_dynamic_task(self, scheduler):
        result = await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 2 hours",
            task_type="dynamic",
            content="What's the weather?",
            reason="Weather check",
            created_by="TestUser",
        )

        assert "dynamic prompt" in result
        assert scheduler.tasks[0].task_type == "dynamic"

    @pytest.mark.asyncio
    async def test_reject_unparseable_time(self, scheduler):
        result = await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="not a real time",
            task_type="static",
            content="Hello!",
            reason="Test",
            created_by="TestUser",
        )

        assert "Could not parse time" in result
        assert len(scheduler.tasks) == 0

    @pytest.mark.asyncio
    async def test_reject_past_time(self, scheduler):
        result = await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 5 seconds",
            task_type="static",
            content="Hello!",
            reason="Test",
            created_by="TestUser",
        )

        assert "at least" in result
        assert len(scheduler.tasks) == 0

    @pytest.mark.asyncio
    async def test_reject_far_future(self, scheduler):
        result = await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 50 days",
            task_type="static",
            content="Hello!",
            reason="Test",
            created_by="TestUser",
        )

        # 50 days > 30 day max
        assert f"{MAX_HORIZON_DAYS} days" in result
        assert len(scheduler.tasks) == 0

    @pytest.mark.asyncio
    async def test_reject_exceeding_guild_limit(self, scheduler):
        # Fill up to the limit
        for i in range(MAX_TASKS_PER_GUILD):
            await scheduler.add_task(
                guild_id=123,
                channel_id=456,
                channel_name="general",
                execute_at_str="in 10 minutes",
                task_type="static",
                content=f"Task {i}",
                reason="Filling up",
                created_by="TestUser",
            )

        assert len(scheduler.tasks) == MAX_TASKS_PER_GUILD

        # Try to add one more
        result = await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="One too many",
            reason="Should fail",
            created_by="TestUser",
        )

        assert f"{MAX_TASKS_PER_GUILD}" in result
        assert len(scheduler.tasks) == MAX_TASKS_PER_GUILD

    @pytest.mark.asyncio
    async def test_guild_limit_is_per_guild(self, scheduler):
        # Fill guild 123
        for i in range(MAX_TASKS_PER_GUILD):
            await scheduler.add_task(
                guild_id=123,
                channel_id=456,
                channel_name="general",
                execute_at_str="in 10 minutes",
                task_type="static",
                content=f"Task {i}",
                reason="Filling",
                created_by="TestUser",
            )

        # Different guild should still work
        result = await scheduler.add_task(
            guild_id=999,
            channel_id=789,
            channel_name="other",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="Different guild",
            reason="Test",
            created_by="TestUser",
        )

        assert "Scheduled" in result
        assert len(scheduler.tasks) == MAX_TASKS_PER_GUILD + 1


class TestSchedulerListTasks:
    @pytest.fixture
    def scheduler(self, tmp_path, monkeypatch):
        monkeypatch.setattr("summarizer_bot.scheduler.TASKS_FILE", str(tmp_path / "tasks.json"))
        bot = make_mock_bot()
        s = Scheduler(bot)
        return s

    @pytest.mark.asyncio
    async def test_list_empty(self, scheduler):
        result = scheduler.list_tasks(guild_id=123)
        assert "No scheduled tasks" in result

    @pytest.mark.asyncio
    async def test_list_with_tasks(self, scheduler):
        await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="Hello!",
            reason="Test",
            created_by="Alice",
        )
        await scheduler.add_task(
            guild_id=123,
            channel_id=789,
            channel_name="random",
            execute_at_str="in 1 hour",
            task_type="dynamic",
            content="Check something",
            reason="Lookup",
            created_by="Bob",
        )

        result = scheduler.list_tasks(guild_id=123)
        assert "2" in result
        assert "general" in result
        assert "random" in result
        assert "Alice" in result
        assert "Bob" in result

    @pytest.mark.asyncio
    async def test_list_filters_by_guild(self, scheduler):
        await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="Guild 123",
            reason="Test",
            created_by="Alice",
        )
        await scheduler.add_task(
            guild_id=999,
            channel_id=789,
            channel_name="other",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="Guild 999",
            reason="Test",
            created_by="Bob",
        )

        result = scheduler.list_tasks(guild_id=123)
        assert "general" in result
        assert "other" not in result


class TestSchedulerCancelTask:
    @pytest.fixture
    def scheduler(self, tmp_path, monkeypatch):
        monkeypatch.setattr("summarizer_bot.scheduler.TASKS_FILE", str(tmp_path / "tasks.json"))
        bot = make_mock_bot()
        s = Scheduler(bot)
        return s

    @pytest.mark.asyncio
    async def test_cancel_existing_task(self, scheduler):
        await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="To be cancelled",
            reason="Test",
            created_by="Alice",
        )

        task_id = scheduler.tasks[0].id
        result = await scheduler.cancel_task(guild_id=123, task_id=task_id)

        assert "Cancelled" in result
        assert task_id in result
        assert len(scheduler.tasks) == 0

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, scheduler):
        result = await scheduler.cancel_task(guild_id=123, task_id="nonexist")
        assert "No task found" in result

    @pytest.mark.asyncio
    async def test_cancel_wrong_guild(self, scheduler):
        await scheduler.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="Hello",
            reason="Test",
            created_by="Alice",
        )

        task_id = scheduler.tasks[0].id
        # Try cancelling from wrong guild
        result = await scheduler.cancel_task(guild_id=999, task_id=task_id)
        assert "No task found" in result
        assert len(scheduler.tasks) == 1  # still there


class TestSchedulerPersistence:
    @pytest.mark.asyncio
    async def test_save_and_load(self, tmp_path, monkeypatch):
        tasks_file = str(tmp_path / "tasks.json")
        monkeypatch.setattr("summarizer_bot.scheduler.TASKS_FILE", tasks_file)

        bot = make_mock_bot()
        s1 = Scheduler(bot)

        await s1.add_task(
            guild_id=123,
            channel_id=456,
            channel_name="general",
            execute_at_str="in 10 minutes",
            task_type="static",
            content="Persist me",
            reason="Test persistence",
            created_by="Alice",
        )

        # Create a new scheduler — should load from disk
        s2 = Scheduler(bot)
        assert len(s2.tasks) == 1
        assert s2.tasks[0].content == "Persist me"
        assert s2.tasks[0].created_by == "Alice"
