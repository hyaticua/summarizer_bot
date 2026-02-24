# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Discord bot for summarizing conversations using large language models (LLMs). The bot can participate in Discord conversations, summarize chat history, and be configured with different personas.

## Environment Setup

Required environment variables:
- `DISCORD_API_KEY`: Discord bot token
- `ANTHROPIC_API_KEY`: Anthropic API key for Claude models (primary)
- `OPENAI_API_KEY`: OpenAI API key (optional, legacy support)

Install dependencies:
```bash
pip install -r requirements.txt
```

Run the bot:
```bash
python -m summarizer_bot.main
```

## Architecture

### Core Components

**bot.py (ChatBot class)**
- Extends `discord.bot.Bot` with LLM integration
- Handles automatic chat responses when bot is mentioned or in DMs
- Fetches and processes Discord messages, converting them to LLM-compatible format
- Unpacks `LLMResponse` from the LLM client: text is sent as the reply, `FileAttachment`s are converted to `discord.File` objects and attached (max 10 per Discord limit)
- When file attachments are present and a status message exists, the status message is deleted and a fresh reply is sent (because `Message.edit()` cannot add file attachments)
- Manages per-server configuration via `Config` class
- Message limit: 1000 messages max, default 50 for chat context

**summarizer.py (LLM Clients)**
- `AnthropicClient`: Primary client using Claude Sonnet 4.6 (default model: `claude-sonnet-4-6`)
- `OpenAIClient`: Legacy support for GPT-4o
- `FileAttachment`: Dataclass holding downloaded file bytes + filename
- `LLMResponse`: Dataclass returned by `generate_as_chat_turns_with_search()` containing `text` and `files: list[FileAttachment]`
- Three generation modes:
  - `generate()`: Simple prompt/response for summarization
  - `generate_as_chat_turns()`: Multi-turn conversation (no tools)
  - `generate_as_chat_turns_with_search()`: Streaming multi-turn with web search, web fetch, code execution, and Discord tool use. Returns `LLMResponse`.
- Streaming uses `client.beta.messages.stream()` with `betas=["files-api-2025-04-14"]` to enable file outputs from code execution
- Streaming loop in `_stream_with_search()` handles multiple stop reasons:
  - `pause_turn`: server-side tool continuation — web search, web fetch, or code execution (up to `MAX_CONTINUATIONS = 3`)
  - `tool_use`: client-side Discord tool execution (up to `MAX_TOOL_ROUNDS = 3`)
  - `end_turn`: normal completion
- After all iterations, file IDs are extracted from code execution result blocks and downloaded via `client.beta.files`
- `_extract_text()`: Extracts text blocks and code execution stdout (prefixed with "**Code output:**")
- `_extract_file_ids()`: Scans response for code execution result blocks (`code_execution_tool_result`, `bash_code_execution_tool_result`, `text_editor_code_execution_tool_result`) and extracts file IDs. Handles both dict and object content formats.
- `_download_files()`: Downloads files from Anthropic's Files API (`client.beta.files.retrieve_metadata` + `client.beta.files.download`). Skips files > 8MB (Discord limit). Errors are logged and skipped gracefully.
- Status callbacks fire during streaming to show the user what's happening (e.g., "Thinking...", "Searching the web...", "Fetching https://....", "Running code...", "Checking voice channels...")
- Server-side tools:
  - `web_search_20250305`: Web search with max 5 uses per request
  - `web_fetch_20250910`: Web page/PDF fetching with max 3 uses per request
  - `code_execution_20250825`: Sandboxed code execution (Bash + file operations); produces file outputs (charts, CSVs, etc.) that are downloaded and attached to Discord replies
- **Known limitation**: `web_search_20260209` and `web_fetch_20260209` auto-inject a legacy `code_execution` tool that conflicts with explicit `code_execution_20250825` (name collision). Older tool versions (`web_search_20250305`, `web_fetch_20250910`) do not have this conflict. If upgrading web search/fetch versions, the explicit code execution tool must be removed (losing file output support).

**message.py (Message Processing)**
- `Message` class: Represents Discord messages with support for:
  - Text content parsing via `format_message_text()` (mention resolution, reply context)
  - Image attachments (base64-encoded for LLM vision capabilities)
  - Reaction data: fetched via `Reaction.users()` in `create()`, stored as `list[tuple[str, list[str]]]` (emoji, user names), included in `to_json()` output. Bot's own reactions are skipped.
  - JSON serialization for structured LLM input
  - Distinction between bot's own messages (`from_self`) and user messages
- `format_message_text()`: Shared formatting function used by both the chat context and Discord tools. Handles:
  - User mention resolution (`<@123>` → `<@DisplayName>`)
  - Channel mention resolution (`<#123>` → `#channel-name`)
  - Reply-to context (`[replying to Author]` prefix)
  - Optional content truncation and attachment filename listing
  - Reaction rendering: appends `[reactions: 👍 x3, ❤️ x1]` (count-based) from `msg.reactions`
- `UserProfile`: Optional user info that can be included in LLM context
- `parse_response()`: Converts LLM output back to Discord mentions (e.g., `<@username>` → `<@user_id>`)

**discord_tools.py (Discord Tool Use)**
- Implements Anthropic's client-side tool_use protocol for querying and acting on Discord state
- `ALL_DISCORD_TOOLS`: Tool definitions in Anthropic API format:
  - `get_server_members`: List members (all, in voice, or recently active in a channel)
  - `list_channels`: List all channels organized by category with voice occupancy
  - `read_channel_history`: Read recent messages from any channel/thread (max 50, capped at 4000 chars). Supports optional filters:
    - `author`: Filter by user (fuzzy-matched via `attempt_to_find_member`)
    - `contains`: Keyword/phrase search (case-insensitive)
    - `before`/`after`: Time filters (relative like "yesterday", "2 hours ago" or absolute like "2024-01-15") parsed by `_parse_time_expression()`
    - `has_attachments`: Only messages with attachments
    - `exclude_bots`: Skip bot messages
    - All filters combine with AND logic; when active, up to `SCAN_LIMIT=500` messages are scanned in batches of `BATCH_SIZE=100`
    - `before`/`after` are passed to `channel.history()` for server-side filtering; other filters applied client-side
  - `delete_messages`: Delete messages in a channel. Supports single ID (`message_id`), batch IDs (`message_ids` array, max 10, deduplicated with `message_id`), or by count to delete the bot's own recent messages (max 5). Own messages always allowed; others' require `manage_messages`. Batch results use per-item error handling via `_format_batch_results()`
  - `timeout_member`: Temporarily timeout a member. Fuzzy-finds the member, parses a human-readable duration via `_parse_duration()`, guards against bots/self/role hierarchy, calls `member.timeout_for()`
  - `schedule_message`: Schedule a static message or dynamic LLM prompt for future execution. Resolves channel, validates time/limits, delegates to `Scheduler.add_task()`
  - `manage_scheduled`: List or cancel scheduled tasks for the guild. Delegates to `Scheduler.list_tasks()` / `Scheduler.cancel_task()`
  - `react_to_message`: React to messages with emoji. Supports single reaction (`message_id` + `emoji`) or batch mode (`reactions` array, max 20). `channel_name` at the top level serves as default for batch items that omit it. Channel resolution is cached per unique name. Batch results use per-item error handling via `_format_batch_results()`
- `TOOL_PERMISSIONS`: Maps tool names to required guild-level Discord permissions. Tools with unmet permissions are excluded from the LLM's tool list at request time
- `DiscordToolExecutor`: Executes tools against a `discord.Guild`, returns results as strings
  - Constructor accepts `requesting_user` param (display name of the user who triggered the tool) for task attribution
  - `get_available_tools()`: Filters `ALL_DISCORD_TOOLS` by the bot's guild permissions, only exposing tools the bot can actually use
- `_fuzzy_find_channel()`: Channel name resolution (exact → case-insensitive → substring match)
- `_parse_duration()`: Parses "N unit(s)" strings (e.g., "5 minutes", "1 hour") into `timedelta`
- `_format_batch_results()`: Formats batch operation results. Single item returns plain message (backward compat); multiple items return summary like `"Reacted 4/5:\n  OK: ...\n  FAILED: ..."`
- `_status_for_tool()`: Returns user-facing status strings per tool invocation; shows filter details when active (e.g., "Searching #general for messages from Alice containing 'bug'...", "Deleting 3 messages...", "Reacting to 5 messages...", "Timing out Alice...")
- Errors are returned as descriptive strings to the LLM, not raised
- Tools are only offered in guild contexts (not DMs)
- Tool output uses `format_message_text()` from `message.py` for consistent message formatting with the chat context

**scheduler.py (Scheduled Actions)**
- `ScheduledTask` dataclass: `id` (8-char uuid), `guild_id`, `channel_id`, `channel_name`, `execute_at` (ISO 8601 UTC), `task_type` ("static" or "dynamic"), `content`, `reason`, `created_by`, `created_at`
- Persistence via `scheduled_tasks.json` (separate from config.json — transient operational data). Uses `aiofiles` for writes, sync `open()` for startup load
- `_parse_future_time()`: Parses future time expressions — "in N units", "today/tomorrow at HH:MM", and absolute dates via `dateutil`
- `Scheduler` class:
  - Background `asyncio` polling loop (every 30s) started from `bot.on_ready()`, with double-start guard
  - Static execution: `channel.send(content)`
  - Dynamic execution: Builds system prompt, calls `_stream_with_search()` with a `DiscordToolExecutor` so the LLM has full tool access (web search, code execution, Discord tools). Sends result (text + file attachments) to the channel
  - Stale task handling on restart: tasks up to 1 hour past due are executed; older ones are discarded with a log warning
  - Limits: 25 tasks per guild, 30 days max horizon, 1 minute minimum lead time

**config.py (Configuration)**
- JSON-based configuration stored in `config.json`
- Two-level hierarchy:
  - Server configs: Per-guild settings (chat allowlists, custom profiles)
  - User configs: Per-user profiles (registered via `/register_user`)
- Top-level keys for server authorization:
  - `authorized_servers`: list of guild ID ints. When absent, all servers are allowed (backward compat). Once set, only listed servers are served.
  - `unauthorized_mode`: `"ignore"` | `"polite"` | `"leave"` | `"bad_bot"`. Default: `"ignore"`.
  - `polite_declined`: list of guild IDs already sent the one-shot refusal. Cleared when mode changes away from polite or when a server gets authorized.
- Methods: `is_server_authorized()`, `get_authorized_servers()`, `set_authorized_servers()`, `get_unauthorized_mode()`, `set_unauthorized_mode()`, `get_polite_declined()`, `add_polite_declined()`, `clear_polite_declined()`
- Async file I/O using `aiofiles`

**token_estimation.py (Token Counting)**
- `TokenCounter`: Estimates token usage for context window management
- Supports API-based counting (`use_api=True`) or fast local estimation (`use_api=False`)
- Used by `ChatBot.build_context_with_token_limit()` to fit messages within token limits

### Discord Commands (Cogs)

Commands are implemented as mixins in `summarizer_bot/commands/`:

**SummarizeMixin** (`/summarize`):
- Summarizes recent chat history (default: 20 messages)
- Optional `accent` parameter for styled summaries
- Uses per-server profile configuration

**ChatAllowlistMixin** (root user only):
- `/chat_allowlist_add`: Restrict bot chat responses to specific channels
- `/chat_allowlist_remove`: Remove channel from allowlist
- `/chat_allowlist_list`: Show current allowlist
- `/chat_allowlist_clear`: Remove all restrictions
- Root user defined in `main.py` (currently `.namielle`)

**UserProfileMixin** (`/register_user`):
- Allows users to register profile info (max 128 chars, no newlines)
- Included in LLM context for personalized responses

**ServerAuthMixin** (root user only):
- `/server_authorize <server_id>`: Add a server to the authorized list. First use activates the auth system (warns the user).
- `/server_deauthorize <server_id>`: Remove a server from the authorized list.
- `/server_auth_list`: Show authorized servers and current unauthorized mode.
- `/server_auth_mode <mode>`: Set behavior for unauthorized servers (`ignore`, `polite`, `leave`, `bad_bot`). Clears `polite_declined` when switching away from `polite`.

### Persona System

Personas are Markdown files in `summarizer_bot/personas/` with structured sections:
- **Identity**: Role and `{{BOT_NAME}}` placeholder (replaced at runtime with the bot's display name)
- **Personality**: Tone, character traits, and behavioral description
- **Response Guidelines**: Rules for formatting, length, and mentions
- **Tools**: Available capabilities (web search, web fetch, code execution, Discord server tools, image viewing)
- **Examples**: Few-shot examples showing expected input/output behavior

`make_sys_prompt()` in `utils.py` replaces `{{BOT_NAME}}` and appends a dynamic `# Current Context` section with the current date, time, and source channel/thread name.

Current persona is set in `main.py` (currently `personas/mommy.md`).

`bad_bot.md` is a separate chaotic anti-persona used only for unauthorized servers in `bad_bot` mode. It is loaded in `ChatBot.__init__()` from a hardcoded path.

## Key Behaviors

**Server Authorization**:
- Checked in `on_message` after self-skip, before allowlist. DMs bypass (no guild context).
- When `authorized_servers` key is absent from config, all servers are allowed (backward compat).
- When active, only listed servers get normal bot behavior. Unauthorized servers are handled by mode:
  - `ignore`: Silently do nothing (default).
  - `polite`: One-shot refusal message per server (tracked in `polite_declined`), then silent.
  - `leave`: Send refusal message, then `guild.leave()`.
  - `bad_bot`: Chaotic gremlin persona using Haiku (`claude-haiku-4-5-20251001`), no tools, no streaming, 20-message context. Uses `personas/bad_bot.md`.
- Only triggers on bot mention (same as normal chat).
- `bad_bot_client` and `bad_bot_persona` are initialized in `ChatBot.__init__()` alongside the main client.

**Automatic Chat Participation**:
- Bot responds when mentioned or in DMs
- Respects chat allowlists (if configured)
- Builds context with token-aware message selection (newest messages prioritized)
- Processes images in messages for multimodal understanding
- Uses `generate_as_chat_turns_with_search()` with streaming for tool use and status updates
- Status messages shown to user during processing (Thinking, Searching, Fetching, tool actions)

**Tool Use (Anthropic tool protocols)**:
- Web search: Server-side tool handled by Anthropic (triggers `pause_turn` continuations)
- Web fetch: Server-side tool handled by Anthropic for reading URLs shared in conversation (also triggers `pause_turn`); status includes the URL being fetched
- Code execution: Server-side sandboxed Bash/file execution handled by Anthropic (also triggers `pause_turn`); generated files (matplotlib charts, etc.) are downloaded via Files API and attached to the Discord reply
- Discord tools: Client-side tools defined in `discord_tools.py`, triggered by `tool_use` stop reason
  - LLM requests a tool → bot executes it → result returned to LLM → LLM continues
  - Tools only available in guild contexts (not DMs)
  - Permission-gated: `get_available_tools()` checks the bot's guild permissions and only exposes tools the bot can use (e.g., `timeout_member` requires `moderate_members`)
  - Read tools: `get_server_members`, `list_channels`, `read_channel_history`, `manage_scheduled` (no special permissions)
  - Write tools: `delete_messages` (own messages always; others' require `manage_messages` per-channel), `timeout_member` (requires `moderate_members` guild permission), `schedule_message` (no special permissions), `react_to_message` (no special guild permission; per-channel `add_reactions` checked at runtime)
  - Bot does not need `presences` intent; uses voice presence and message activity as proxies

**Scheduled Actions**:
- Users can ask the bot to schedule messages or dynamic prompts for future execution
- Static tasks send content as-is at the scheduled time
- Dynamic tasks process content through the LLM at execution time with full tool access (web search, code execution, Discord tools)
- Background polling loop checks every 30s for due tasks
- Tasks persist across restarts via `scheduled_tasks.json`; tasks up to 1 hour overdue are executed on restart, older ones are discarded
- Limits: 25 tasks per guild, 30-day max horizon, 1-minute minimum lead time

**Message Format for LLM**:
- User messages: Structured JSON with `message_id`, `created_at`, `author`, `content`
- Bot's own messages: Plain text (to simulate natural conversation history)
- Images: Sent as base64-encoded multimodal content
- Content is processed through `format_message_text()` which resolves mentions and adds reply context

## Testing

Run tests:
```bash
python -m pytest tests/ -v
```

Test harness available at `summarizer_bot/test_harness.py` (not yet configured).

## Important Notes

- The bot uses Discord's `py-cord` library, not `discord.py`
- User mentions in Discord use numeric IDs (`<@123456>`), but LLM sees/generates readable names
- Channel mentions in Discord use numeric IDs (`<#123456>`), resolved to `#channel-name` by `parse_content()`
- Streaming uses the beta messages endpoint (`client.beta.messages.stream`) with the `files-api-2025-04-14` beta flag for code execution file support
- Config updates are immediately persisted to `config.json`
- Message processing skips bots by default (except when they're replies)
- `discord_tools.py` uses a `try/except ImportError` pattern for imports from `message.py` to support both bare imports (runtime from package directory) and package imports (test suite)
