# summarizer_bot
Discord bot for summarizing conversations using a large language model.

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
