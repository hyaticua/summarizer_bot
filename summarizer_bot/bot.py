import io
import os

import discord
from loguru import logger
from config import Config
from summarizer import AnthropicClient
from utils import make_sys_prompt
from message import parse_response, UserProfile, Message
from discord_tools import DiscordToolExecutor
from scheduler import Scheduler
from token_estimation import TokenCounter
import time

message_limit = 1000

BAD_BOT_PERSONA_PATH = os.path.join(os.path.dirname(__file__), "personas", "bad_bot.md")
BAD_BOT_CONTEXT_LIMIT = 20


class ChatBot(discord.bot.Bot):
    def __init__(self, root_user, llm_api_key, persona_path=None):
        super().__init__(intents=self._setup_intents())
        self.root_user = root_user
        self.llm_api_key = llm_api_key
        self.persona = self._setup_persona(persona_path)

        self.config = Config.try_init_from_file("config.json")
        self.llm_client = AnthropicClient(self.llm_api_key)
        # use_api=False for fast estimation, use_api=True for accurate API-based counting
        self.token_counter = TokenCounter(self.llm_client.client, self.llm_client.model, use_api=False)
        self.scheduler = Scheduler(self)

        self.bad_bot_client = AnthropicClient(self.llm_api_key, model="claude-haiku-4-5-20251001")
        self.bad_bot_persona = self._setup_persona(BAD_BOT_PERSONA_PATH)

    def _setup_intents(self):
        intents = discord.Intents().default()
        intents.members = True
        return intents
    
    def _setup_persona(self, path):
        logger.info("Loading persona from {}", os.path.abspath(path))
        with open(path, "r") as f:
            return f.read()

    async def _handle_unauthorized(self, message: discord.Message):
        """Handle a message from an unauthorized server. Only acts on mentions."""
        if not (self.user and self.user.mentioned_in(message)):
            return

        mode = self.config.get_unauthorized_mode()
        guild = message.guild

        if mode == "ignore":
            logger.debug("Ignoring mention from unauthorized server {} ({})", guild.name, guild.id)
            return

        elif mode == "polite":
            declined = self.config.get_polite_declined()
            if guild.id in declined:
                logger.debug("Already sent polite refusal to {} ({})", guild.name, guild.id)
                return
            logger.info("Sending polite refusal to {} ({})", guild.name, guild.id)
            await message.reply("Sorry, I'm not set up for this server! Please contact my admin if you think this is a mistake.")
            await self.config.add_polite_declined(guild.id)

        elif mode == "leave":
            logger.info("Sending refusal and leaving {} ({})", guild.name, guild.id)
            await message.reply("Sorry, I'm not set up for this server! I'll see myself out.")
            await guild.leave()

        elif mode == "bad_bot":
            await self._handle_bad_bot(message)

    async def _handle_bad_bot(self, message: discord.Message):
        """Respond as the chaotic bad_bot persona on unauthorized servers."""
        logger.info("bad_bot triggered by {} in {} ({})",
                     message.author.display_name, message.guild.name, message.guild.id)

        raw_messages = await self.fetch_messages(message.channel.id, num_messages=BAD_BOT_CONTEXT_LIMIT)
        messages, _ = await self.process_messages(raw_messages, skip_bots=False)
        # Drop the bot's own messages so Haiku doesn't mimic the main persona's style
        # To undo: comment out the next line
        messages = [m for m in messages if not m.from_self]

        sys_prompt = make_sys_prompt(message.guild, self.bad_bot_persona, channel=message.channel)
        response_text = await self.bad_bot_client.generate_as_chat_turns(messages, sys_prompt)
        response = parse_response(response_text, message.guild)

        await message.reply(response[:2000])

    # overload
    async def on_ready(self):
        logger.info("Logged in as {}", self.user)
        for guild in self.guilds:
            logger.info("Connected to guild: {} (id={})", guild.name, guild.id)
            # One-time nickname reset — remove after next restart
            if guild.me.nick is not None:
                try:
                    await guild.me.edit(nick=None)
                    logger.info("Reset nickname in {} ({})", guild.name, guild.id)
                except discord.errors.Forbidden:
                    logger.warning("Cannot reset nickname in {} ({})", guild.name, guild.id)
        await self.scheduler.start()

    # overload
    async def on_message(self, message: discord.Message):
        try:
            if message.author == self.user:
                return

            # Server authorization gate (DMs bypass)
            if message.guild and not self.config.is_server_authorized(message.guild.id):
                await self._handle_unauthorized(message)
                return

            server_config = self.config.get_server_config(message.guild.id)
            if "chat_allowlist" in server_config and server_config["chat_allowlist"] and message.channel.id not in server_config["chat_allowlist"]:
                return

            if isinstance(message.channel, discord.channel.DMChannel) or (self.user and self.user.mentioned_in(message)):
                is_dm = isinstance(message.channel, discord.channel.DMChannel)
                logger.info(
                    "Chat triggered by {} in {} ({})",
                    message.author.display_name,
                    f"DM" if is_dm else f"#{message.channel.name}",
                    message.guild.name if message.guild else "DM",
                )

                start_time = time.time()

                sys_prompt = make_sys_prompt(message.guild, self.persona, channel=message.channel)

                # Build context with token awareness
                messages = await self.build_context_with_token_limit(
                    message.channel.id,
                    sys_prompt,
                    max_messages=50,
                    enable_token_counting=True
                )
                logger.debug("Built context with {} messages", len(messages))

                sent_msg = None
                statuses = []

                tool_executor = DiscordToolExecutor(message.guild, self, requesting_user=message.author.display_name) if message.guild else None

                async def update_status(status):
                    nonlocal sent_msg
                    statuses.append(status)
                    content = "\n".join(statuses)
                    if sent_msg is None:
                        sent_msg = await message.reply(content)
                        if tool_executor:
                            tool_executor.active_message_id = sent_msg.id
                    else:
                        await sent_msg.edit(content=content)

                llm_response = await self.llm_client.generate_as_chat_turns_with_search(
                    messages, sys_prompt, status_callback=update_status, tool_executor=tool_executor
                )

                response = parse_response(llm_response.text, message.guild)

                # Build Discord file attachments from code execution outputs
                discord_files = []
                for f in llm_response.files[:10]:  # Discord caps at 10 files
                    discord_files.append(discord.File(io.BytesIO(f.data), filename=f.filename))

                if discord_files:
                    # Message.edit() cannot add file attachments — delete status msg and send fresh reply
                    if sent_msg:
                        await sent_msg.delete()
                    await message.reply(response[:2000], files=discord_files)
                elif sent_msg is None:
                    await message.reply(response[:2000])
                else:
                    await sent_msg.edit(content=response[:2000])

                elapsed_time = time.time() - start_time
                logger.info("Response sent in {:.2f}s ({} chars)", elapsed_time, len(response))

        except discord.errors.Forbidden as e:
            logger.warning("Forbidden error in channel {}: {}", message.channel, e)
            await message.author.send("Sorry it looks like I don't have access!")
        except Exception as e:
            logger.exception("Unhandled error processing message from {}", message.author.display_name)

    async def build_context_with_token_limit(
        self,
        channel_id: int,
        sys_prompt: "str | list",
        max_messages: int = 50,
        enable_token_counting: bool = True
    ) -> list[Message]:
        """
        Build message context in reverse (newest to oldest) while staying within token limits.

        This fetches messages and processes them from newest to oldest, adding them to the
        context window until we hit the token limit. This ensures we always include the most
        recent messages and drop the oldest ones if needed.

        Args:
            channel_id: The Discord channel ID to fetch from
            sys_prompt: The system prompt (needed for token counting)
            max_messages: Maximum number of messages to fetch initially
            enable_token_counting: Whether to use token counting (False = just use max_messages)

        Returns:
            List of Message objects in chronological order (oldest to newest)
        """
        # Fetch raw messages
        raw_messages = await self.fetch_messages(channel_id, num_messages=max_messages)

        # If token counting is disabled, just process all messages normally
        if not enable_token_counting:
            messages, _ = await self.process_messages(raw_messages, False)
            return messages

        # Process messages in reverse order (newest first)
        messages_to_include = []
        max_tokens = self.token_counter.get_max_context_tokens()

        final_count = 0

        # Process from newest to oldest
        for raw_msg in reversed(raw_messages):
            # Skip bots and empty messages
            if not raw_msg.content or (raw_msg.author.bot and not raw_msg.reference):
                continue

            # Create the message object
            processed_msg = await Message.create(raw_msg, from_self=raw_msg.author.id == self.user.id)

            # Try adding this message to the beginning of our list
            candidate_messages = [processed_msg] + messages_to_include

            # Check if adding this message would exceed our token limit
            token_count = await self.token_counter.count_tokens(candidate_messages, sys_prompt)

            if token_count <= max_tokens:
                # We're still within limits, add this message
                messages_to_include = candidate_messages
                final_count = token_count
            else:
                # Adding this message would exceed limits, stop here
                # We keep what we have (the newest messages)
                logger.debug("Token limit reached: {} > {} (keeping {} messages)", token_count, max_tokens, len(messages_to_include))
                break

        logger.debug("Final context: {} messages, ~{} tokens / {} max", len(messages_to_include), final_count, max_tokens)
        return messages_to_include

    def get_user_profiles(self, involved_users: set[discord.Member]) -> list[UserProfile]:
        user_profiles = []
        for user in involved_users:
            if self.config.has_user_config(user.id):
                user_info = self.config.get_user_config(user.id)["info"]
                user_profiles.append(UserProfile(user.nick, user_info))
        return user_profiles


    async def fetch_messages(self, channel_id, num_messages = message_limit) -> list[discord.Message]:
        num_messages = min(num_messages, message_limit)

        chan = self.get_channel(channel_id)

        raw_messages = await chan.history(limit=num_messages).flatten()
        raw_messages.reverse()    
        
        return raw_messages
    
    async def process_messages(self, raw_messages: list[discord.Message], skip_bots: bool = True) -> tuple[list[Message], set[discord.Member]]:
        messages = []
        involved_users = set()

        for msg in raw_messages:
            # skip bots and empty messages
            if not msg.content or (skip_bots and msg.author.bot and not msg.reference):
                continue

            processed_msg = await Message.create(msg, from_self=msg.author.id == self.user.id)
            messages.append(processed_msg)
            involved_users.add(msg.author)

        return messages, involved_users
