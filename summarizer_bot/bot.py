import discord
from config import Config
from summarizer import AnthropicClient
import json
from utils import make_sys_prompt
from message import parse_response, UserProfile, Message

message_limit = 1000


class ChatBot(discord.bot.Bot):
    def __init__(self, root_user, llm_api_key, persona_path=None):
        super().__init__(intents=self._setup_intents())
        self.root_user = root_user
        self.llm_api_key = llm_api_key
        self.persona = self._setup_persona(persona_path)

        self.config = Config.try_init_from_file("config.json")
        self.llm_client = AnthropicClient(self.llm_api_key)

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
                
            if isinstance(message.channel, discord.channel.DMChannel) or (self.user and self.user.mentioned_in(message)):
                raw_messages = await self.fetch_messages(message.channel.id, num_messages=50)
                messages, involved_users = await self.process_messages(raw_messages, False)

                sys_prompt = make_sys_prompt(message.guild, self.persona)

                raw_response = await self.llm_client.generate_as_chat_turns(messages, sys_prompt)

                response = parse_response(raw_response, message.guild)    
                await message.reply(response)

        except discord.errors.Forbidden as e:
            await message.author.send("Sorry it looks like I don't have access!")

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
