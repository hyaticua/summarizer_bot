import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import aiofiles
from dateutil import parser as dateutil_parser
from loguru import logger

TASKS_FILE = "scheduled_tasks.json"
MAX_TASKS_PER_GUILD = 25
MAX_HORIZON_DAYS = 30
MIN_LEAD_SECONDS = 60
POLL_INTERVAL = 30
STALE_THRESHOLD = timedelta(hours=1)


@dataclass
class ScheduledTask:
    id: str
    guild_id: int
    channel_id: int
    channel_name: str
    execute_at: str  # ISO 8601 UTC
    task_type: str  # "static" or "dynamic"
    content: str
    reason: str
    created_by: str
    created_at: str  # ISO 8601 UTC

    @property
    def execute_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.execute_at)

    @property
    def created_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.created_at)


def _parse_future_time(expr: str) -> datetime | None:
    """Parse a human-readable time expression into a future UTC datetime.

    Supports:
    - "in N units" -> now + timedelta
    - "tomorrow at HH:MM" / "today at HH:MM"
    - Absolute dates via dateutil (e.g. "2026-03-01 14:00", "March 1 at 2pm")

    Returns None if the expression cannot be parsed.
    """
    expr = expr.strip().lower()
    now = datetime.now(timezone.utc)

    # "in N units" pattern
    match = re.match(r"in\s+(\d+)\s+(second|minute|hour|day|week)s?$", expr)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        unit_map = {
            "second": timedelta(seconds=1),
            "minute": timedelta(minutes=1),
            "hour": timedelta(hours=1),
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
        }
        return now + unit_map[unit] * amount

    # "tomorrow at HH:MM" / "today at HH:MM"
    match = re.match(r"(today|tomorrow)\s+at\s+(.+)$", expr)
    if match:
        day_word = match.group(1)
        time_str = match.group(2).strip()
        try:
            parsed_time = dateutil_parser.parse(time_str)
        except (ValueError, OverflowError):
            return None
        base = now if day_word == "today" else now + timedelta(days=1)
        result = base.replace(
            hour=parsed_time.hour,
            minute=parsed_time.minute,
            second=0,
            microsecond=0,
        )
        return result

    # Absolute date/time via dateutil
    try:
        parsed = dateutil_parser.parse(expr)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, OverflowError):
        return None


class Scheduler:
    def __init__(self, bot):
        self.bot = bot
        self.tasks: list[ScheduledTask] = []
        self._loop_task: asyncio.Task | None = None
        self._started = False
        self._load_tasks()

    def _load_tasks(self):
        """Load tasks from disk at startup (sync, before event loop)."""
        try:
            with open(TASKS_FILE, "r") as f:
                data = json.load(f)
            self.tasks = [ScheduledTask(**t) for t in data]
            logger.info("Loaded {} scheduled tasks from {}", len(self.tasks), TASKS_FILE)
        except FileNotFoundError:
            logger.info("No scheduled tasks file found, starting fresh")
        except Exception as e:
            logger.error("Failed to load scheduled tasks: {}", e)

    async def _save_tasks(self):
        """Persist tasks to disk."""
        data = [asdict(t) for t in self.tasks]
        async with aiofiles.open(TASKS_FILE, mode="w") as f:
            await f.write(json.dumps(data, indent=2))

    async def start(self):
        """Start the background polling loop. Safe to call multiple times (guards against double-start)."""
        if self._started:
            logger.debug("Scheduler already started, skipping")
            return
        self._started = True

        # Handle stale tasks from previous run
        await self._handle_stale_tasks()

        self._loop_task = asyncio.create_task(self._poll_loop())
        logger.info("Scheduler started (polling every {}s)", POLL_INTERVAL)

    async def _poll_loop(self):
        """Background loop that checks for and executes due tasks."""
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                await self._check_due_tasks()
            except asyncio.CancelledError:
                logger.info("Scheduler poll loop cancelled")
                break
            except Exception as e:
                logger.exception("Error in scheduler poll loop: {}", e)

    async def _handle_stale_tasks(self):
        """On startup, execute tasks up to 1 hour past due; discard older ones."""
        now = datetime.now(timezone.utc)
        to_execute = []
        to_discard = []
        remaining = []

        for task in self.tasks:
            if task.execute_at_dt <= now:
                age = now - task.execute_at_dt
                if age <= STALE_THRESHOLD:
                    to_execute.append(task)
                else:
                    to_discard.append(task)
            else:
                remaining.append(task)

        for task in to_discard:
            logger.warning(
                "Discarding stale task {} (was due {}, {} overdue)",
                task.id, task.execute_at, now - task.execute_at_dt,
            )

        self.tasks = remaining
        if to_discard:
            await self._save_tasks()

        for task in to_execute:
            logger.info("Executing overdue task {} (due {})", task.id, task.execute_at)
            await self._execute_task(task)

    async def _check_due_tasks(self):
        """Check for and execute any tasks that are now due."""
        now = datetime.now(timezone.utc)
        due = [t for t in self.tasks if t.execute_at_dt <= now]
        if not due:
            return

        for task in due:
            self.tasks.remove(task)
            logger.info("Executing scheduled task {} (type={}, channel={})",
                        task.id, task.task_type, task.channel_name)
            await self._execute_task(task)

        await self._save_tasks()

    async def _execute_task(self, task: ScheduledTask):
        """Execute a single scheduled task."""
        try:
            guild = self.bot.get_guild(task.guild_id)
            if not guild:
                logger.warning("Guild {} not found for task {}", task.guild_id, task.id)
                return

            channel = guild.get_channel(task.channel_id)
            if not channel:
                logger.warning("Channel {} not found for task {}", task.channel_id, task.id)
                return

            if task.task_type == "static":
                await self._execute_static(channel, task)
            elif task.task_type == "dynamic":
                await self._execute_dynamic(guild, channel, task)
            else:
                logger.error("Unknown task type: {}", task.task_type)
        except Exception as e:
            logger.exception("Failed to execute task {}: {}", task.id, e)

    async def _execute_static(self, channel, task: ScheduledTask):
        """Send a static message to the channel."""
        await channel.send(task.content)
        logger.info("Static task {} executed in #{}", task.id, task.channel_name)

    async def _execute_dynamic(self, guild, channel, task: ScheduledTask):
        """Process a dynamic prompt through the LLM with full tool access."""
        import io

        import discord

        try:
            from discord_tools import DiscordToolExecutor
            from message import parse_response
            from utils import make_sys_prompt
        except ImportError:
            from .discord_tools import DiscordToolExecutor
            from .message import parse_response
            from .utils import make_sys_prompt

        sys_prompt = make_sys_prompt(guild, self.bot.persona, channel=channel)
        messages = [{"role": "user", "content": task.content}]

        tool_executor = DiscordToolExecutor(guild, self.bot, requesting_user=task.created_by)

        try:
            llm_response = await self.bot.llm_client._stream_with_search(
                messages, sys_prompt, status_callback=None, tool_executor=tool_executor
            )

            response = parse_response(llm_response.text, guild)

            discord_files = []
            for f in llm_response.files[:10]:
                discord_files.append(discord.File(io.BytesIO(f.data), filename=f.filename))

            if discord_files:
                await channel.send(response[:2000], files=discord_files)
            elif response:
                await channel.send(response[:2000])
            else:
                logger.warning("Dynamic task {} produced empty response", task.id)

            logger.info("Dynamic task {} executed in #{}", task.id, task.channel_name)
        except Exception as e:
            logger.exception("Dynamic task {} failed: {}", task.id, e)
            await channel.send(f"Sorry, I tried to run a scheduled task but something went wrong: {e}")

    # ---- Public API used by discord_tools.py ----

    async def add_task(
        self,
        guild_id: int,
        channel_id: int,
        channel_name: str,
        execute_at_str: str,
        task_type: str,
        content: str,
        reason: str,
        created_by: str,
    ) -> str:
        """Add a new scheduled task. Returns a success message or error string."""
        # Parse the time
        execute_at = _parse_future_time(execute_at_str)
        if execute_at is None:
            return f"Could not parse time: '{execute_at_str}'. Try 'in 2 hours', 'tomorrow at 9am', or '2026-03-01 14:00'."

        now = datetime.now(timezone.utc)

        # Minimum lead time
        if (execute_at - now).total_seconds() < MIN_LEAD_SECONDS:
            return f"Tasks must be scheduled at least {MIN_LEAD_SECONDS // 60} minute(s) in the future."

        # Maximum horizon
        if (execute_at - now).days > MAX_HORIZON_DAYS:
            return f"Tasks cannot be scheduled more than {MAX_HORIZON_DAYS} days in the future."

        # Per-guild limit
        guild_tasks = [t for t in self.tasks if t.guild_id == guild_id]
        if len(guild_tasks) >= MAX_TASKS_PER_GUILD:
            return f"This server already has {MAX_TASKS_PER_GUILD} scheduled tasks. Cancel some before adding more."

        task = ScheduledTask(
            id=uuid.uuid4().hex[:8],
            guild_id=guild_id,
            channel_id=channel_id,
            channel_name=channel_name,
            execute_at=execute_at.isoformat(),
            task_type=task_type,
            content=content,
            reason=reason,
            created_by=created_by,
            created_at=now.isoformat(),
        )
        self.tasks.append(task)
        await self._save_tasks()

        time_str = execute_at.strftime("%Y-%m-%d %H:%M UTC")
        return f"Scheduled {'dynamic prompt' if task_type == 'dynamic' else 'message'} in #{channel_name} for {time_str} (task ID: {task.id}). Reason: {reason}"

    def list_tasks(self, guild_id: int) -> str:
        """List all scheduled tasks for a guild."""
        guild_tasks = [t for t in self.tasks if t.guild_id == guild_id]
        if not guild_tasks:
            return "No scheduled tasks for this server."

        guild_tasks.sort(key=lambda t: t.execute_at)
        lines = []
        for t in guild_tasks:
            time_str = t.execute_at_dt.strftime("%Y-%m-%d %H:%M UTC")
            preview = t.content[:60] + ("..." if len(t.content) > 60 else "")
            lines.append(
                f"- **{t.id}** | {time_str} | {t.task_type} | #{t.channel_name} | {preview} (by {t.created_by})"
            )

        return f"Scheduled tasks ({len(guild_tasks)}):\n" + "\n".join(lines)

    async def cancel_task(self, guild_id: int, task_id: str) -> str:
        """Cancel a scheduled task by ID."""
        for i, t in enumerate(self.tasks):
            if t.id == task_id and t.guild_id == guild_id:
                removed = self.tasks.pop(i)
                await self._save_tasks()
                return f"Cancelled task {removed.id} (was scheduled for {removed.execute_at_dt.strftime('%Y-%m-%d %H:%M UTC')} in #{removed.channel_name})."

        return f"No task found with ID '{task_id}' in this server."
