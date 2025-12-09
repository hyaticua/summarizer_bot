import discord
from config import Config
from summarizer import AnthropicClient
import json
from utils import make_sys_prompt
from message import parse_response, UserProfile, Message
from datetime import datetime, timedelta

message_limit = 1000


class ChatBot(discord.bot.Bot):
    def __init__(self, root_user, llm_api_key, persona_path=None):
        super().__init__(intents=self._setup_intents())
        self.root_user = root_user
        self.llm_api_key = llm_api_key
        self.persona = self._setup_persona(persona_path)

        self.config = Config.try_init_from_file("config.json")
        self.llm_client = AnthropicClient(self.llm_api_key)

        # Track last auto-response time per channel for cooldown
        self.last_auto_response = {}

    def _setup_intents(self):
        intents = discord.Intents().default()
        intents.members = True
        return intents
    
    def _setup_persona(self, path):
        with open(path, "r") as f:
            persona = json.load(f)
        return persona
    
    # overload
    async def on_ready(self):
        print(f"We have logged in as {self.user}")
        for guild in self.guilds:
            print(f"{guild.id=} {guild.name=}")

    # overload
    async def on_message(self, message: discord.Message):
        try:
            if message.author == self.user:
                return

            server_config = self.config.get_server_config(message.guild.id)
            if "chat_allowlist" in server_config and server_config["chat_allowlist"] and message.channel.id not in server_config["chat_allowlist"]:
                return

            # Check if this is an explicit mention/DM or auto-response
            is_explicit = isinstance(message.channel, discord.channel.DMChannel) or (self.user and self.user.mentioned_in(message))

            if is_explicit:
                # Original behavior: respond to mentions and DMs
                await self.generate_and_send_response(message.channel.id, message.guild, reply_to=message)
            else:
                # New behavior: check if we should auto-respond
                chattiness_config = self.config.get_chattiness_config(message.guild.id)

                if chattiness_config["enabled"]:
                    should_respond = await self.should_auto_respond(message, chattiness_config)

                    if should_respond:
                        # Generate and send response without replying for more natural feel
                        await self.generate_and_send_response(message.channel.id, message.guild)

                        # Track auto-response time
                        self.last_auto_response[message.channel.id] = datetime.now()

        except discord.errors.Forbidden as e:
            await message.author.send("Sorry it looks like I don't have access!")

    def get_user_profiles(self, involved_users: set[discord.Member]) -> list[UserProfile]:
        user_profiles = []
        for user in involved_users:
            if self.config.has_user_config(user.id):
                user_info = self.config.get_user_config(user.id)["info"]
                user_profiles.append(UserProfile(user.nick, user_info))
        return user_profiles

    async def generate_and_send_response(self, channel_id: int, guild: discord.Guild, reply_to: discord.Message = None) -> None:
        """Fetch context, generate response, and send it."""
        raw_messages = await self.fetch_messages(channel_id, num_messages=50)
        messages, involved_users = await self.process_messages(raw_messages, False)

        sys_prompt = make_sys_prompt(guild, self.persona)

        raw_response = await self.llm_client.generate_as_chat_turns(messages, sys_prompt)

        response = parse_response(raw_response, guild)

        if reply_to:
            await reply_to.reply(response)
        else:
            channel = self.get_channel(channel_id)
            await channel.send(response)

    async def should_auto_respond(self, message: discord.Message, chattiness_config: dict) -> bool:
        """Check heuristics and use LLM to decide if bot should auto-respond."""

        # Heuristic 1: Check cooldown
        channel_id = message.channel.id
        if channel_id in self.last_auto_response:
            time_since_last = datetime.now() - self.last_auto_response[channel_id]
            cooldown = timedelta(seconds=chattiness_config["cooldown_seconds"])
            if time_since_last < cooldown:
                return False

        # Heuristic 2: Skip very short messages
        if len(message.content) < chattiness_config["min_message_length"]:
            return False

        # Heuristic 3: Check if enough messages have passed since bot last spoke
        raw_messages = await self.fetch_messages(channel_id, num_messages=20)
        messages_since_bot = 0
        for msg in reversed(raw_messages):
            if msg.author.id == self.user.id:
                break
            messages_since_bot += 1

        if messages_since_bot < chattiness_config["min_messages_since_last_response"]:
            return False

        # Heuristic 4: Require multiple messages in conversation (not just one person talking)
        if chattiness_config.get("require_multiple_messages", True):
            recent_authors = set(msg.author.id for msg in raw_messages[-5:] if not msg.author.bot)
            if len(recent_authors) < 2:
                return False

        # All heuristics passed, now ask Haiku for decision
        processed_messages, _ = await self.process_messages(raw_messages[-10:], skip_bots=False)
        decision = await self.llm_client.should_respond(processed_messages, self.user.display_name)

        return decision


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
