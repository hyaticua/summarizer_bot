import os

from bot import ChatBot
from commands import ChatAllowlistMixin, SummarizeMixin, UserProfileMixin, ChattinessMixin 

root_user = ".namielle"
persona = "summarizer_bot/personas/mommy.json"

discord_api_key = os.environ.get("DISCORD_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

print(f"{discord_api_key=}")
print(f"{openai_api_key=}")
print(f"{anthropic_api_key=}")

bot = ChatBot(root_user=root_user, llm_api_key=anthropic_api_key, persona_path=persona)
bot.add_cog(ChatAllowlistMixin(bot))
bot.add_cog(SummarizeMixin(bot))
bot.add_cog(UserProfileMixin(bot))
bot.add_cog(ChattinessMixin(bot))

def run():
    bot.run(discord_api_key)

if __name__ == "__main__":
    run()
