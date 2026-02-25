# summarizer_bot

Discord bot that summarizes conversations, participates in chat, and interacts with server state using LLMs. Built with [py-cord](https://github.com/Pycord-Development/pycord) and [Anthropic's Claude API](https://docs.anthropic.com/en/docs).

## Setup

### Requirements

- Python 3.10+
- A [Discord bot application](https://discord.com/developers/applications) with a bot token
- An [Anthropic API key](https://console.anthropic.com/)

### Installation

```bash
git clone <repo-url>
cd summarizer_bot
pip install -r requirements.txt
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_API_KEY` | Yes | Discord bot token |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (Claude) |
| `OPENAI_API_KEY` | No | OpenAI API key (legacy support) |

Set these however you prefer — `.env` file, shell exports, process manager config, etc. The bot reads them from the environment at startup.

### Running

```bash
python -m summarizer_bot.main
```

Logs are written to `logs/bot.log` (rotated at 25 MB, retained 7 days) and stdout.

## Configuration

Runtime configuration is stored in `config.json` (created automatically). This includes per-server settings like chat allowlists and custom user profiles.

Scheduled tasks are stored separately in `scheduled_tasks.json`.

### Persona

The bot's personality is defined by a Markdown file in `summarizer_bot/personas/`. The active persona is set in `main.py`. The persona file uses `{{BOT_NAME}}` as a placeholder, replaced at runtime with the bot's display name.

## Slash Commands

| Command | Access | Description |
|---|---|---|
| `/summarize` | Everyone | Summarize recent chat history |
| `/register_user` | Everyone | Register a short profile blurb included in LLM context |
| `/chat_allowlist_add` | Root user | Restrict bot responses to specific channels |
| `/chat_allowlist_remove` | Root user | Remove a channel from the allowlist |
| `/chat_allowlist_list` | Root user | Show current allowlist |
| `/chat_allowlist_clear` | Root user | Clear all allowlist restrictions |
| `/server_authorize` | Root user | Add a server to the authorized list |
| `/server_deauthorize` | Root user | Remove a server from the authorized list |
| `/server_auth_list` | Root user | Show authorized servers and mode |
| `/server_auth_mode` | Root user | Set behavior for unauthorized servers |

The root user is configured in `main.py`.

## LLM Capabilities

When mentioned or DMed, the bot responds using Claude with access to:

- **Web search** — looks things up on the web
- **Web fetch** — reads URLs shared in conversation
- **Code execution** — runs code in a sandbox, can generate and attach files (charts, CSVs, etc.)
- **Discord tools** — reads channels, lists members, checks voice state, reads message history, deletes messages, timeouts members, schedules messages, reacts to messages

Tool availability is automatically gated by the bot's Discord permissions in each server.

## Discord Permissions

### Gateway Intents

These must be enabled in the [Discord Developer Portal](https://discord.com/developers/applications) under **Bot > Privileged Gateway Intents**:

| Intent | Required | Why |
|---|---|---|
| Server Members | Yes | Listing server members, fuzzy-matching usernames for mentions and tool use |
| Message Content | Yes | Reading message text to build LLM context and respond to mentions |
| Presence | No | Not used — the bot uses voice channel presence and message activity instead |

### Bot Permissions

Minimum permissions needed for core functionality:

| Permission | Features |
|---|---|
| Send Messages | Responding to mentions, sending scheduled messages |
| Read Message History | Building chat context, `/summarize`, reading channel history via tools |
| Embed Links | Formatting rich responses |
| Attach Files | Sending code execution outputs (charts, CSVs, etc.) |

### Feature-Specific Permissions

These permissions unlock additional tool capabilities. Features are automatically hidden from the LLM if the bot lacks the required permission.

| Permission | Feature | Tool |
|---|---|---|
| Manage Messages | Delete other users' messages | `delete_messages` |
| Moderate Members | Timeout server members | `timeout_member` |

The following features require **no special permissions** beyond the base set above:

| Feature | Tool |
|---|---|
| List server members | `get_server_members` |
| List channels | `list_channels` |
| Read channel history | `read_channel_history` |
| Schedule messages / dynamic prompts | `schedule_message` |
| List / cancel scheduled tasks | `manage_scheduled` |

## Testing

```bash
python -m pytest tests/ -v
```

## Project Structure

```
summarizer_bot/
├── main.py              # Entry point, logging setup, cog registration
├── bot.py               # ChatBot — Discord event handling, LLM integration
├── summarizer.py        # Anthropic/OpenAI clients, streaming, tool loop
├── message.py           # Discord message parsing and formatting
├── discord_tools.py     # Client-side Discord tools for the LLM
├── scheduler.py         # Scheduled message/prompt execution
├── config.py            # Per-server and per-user JSON config
├── memory.py            # Bot memory system
├── token_estimation.py  # Token counting for context window management
├── utils.py             # Persona loading, system prompt construction
├── commands/            # Slash command cogs (summarize, allowlist, auth, etc.)
└── personas/            # Persona markdown files
```
