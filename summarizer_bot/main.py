import logging
import os
import sys

from loguru import logger

# ---------------------------------------------------------------------------
# Loguru configuration
# ---------------------------------------------------------------------------
# Remove the default stderr sink so we can reconfigure it
logger.remove()

# Console sink — DEBUG and above; auto-detect color (disabled when piped, e.g. pm2)
logger.add(sys.stdout, level="DEBUG")

# Rotating file sink — DEBUG and above, 25 MB per file, keep 7 days, compress old
logger.add(
    "logs/bot.log",
    level="DEBUG",
    rotation="25 MB",
    retention="7 days",
    compression="gz",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
)

# Route stdlib logging (used by discord/py-cord, httpx, etc.) into loguru
class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        # Map stdlib level to loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())

logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)

# ---------------------------------------------------------------------------

from bot import ChatBot
from commands import ChatAllowlistMixin, MemoryMixin, ServerAuthMixin, SummarizeMixin, UserProfileMixin

root_user = ".namielle"
persona = "summarizer_bot/personas/mommy.md"

discord_api_key = os.environ.get("DISCORD_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

logger.info("API keys loaded: DISCORD={}, OPENAI={}, ANTHROPIC={}",
            "set" if discord_api_key else "MISSING",
            "set" if openai_api_key else "MISSING",
            "set" if anthropic_api_key else "MISSING")

bot = ChatBot(root_user=root_user, llm_api_key=anthropic_api_key, persona_path=persona)
bot.add_cog(ChatAllowlistMixin(bot))
bot.add_cog(MemoryMixin(bot))
bot.add_cog(ServerAuthMixin(bot))
bot.add_cog(SummarizeMixin(bot))
bot.add_cog(UserProfileMixin(bot))

def run():
    logger.info("Starting bot...")
    bot.run(discord_api_key)

if __name__ == "__main__":
    run()
