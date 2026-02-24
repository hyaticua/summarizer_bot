import aiofiles
import json
from collections import defaultdict
from loguru import logger

class Config:
    def __init__(self, global_config: dict):
        self.global_config = global_config

    @staticmethod
    def try_init_from_file(path: str) -> "Config":
        global_config = defaultdict(dict)

        try:
            with open(path, "r") as f:
                global_config.update(json.loads(f.read()))
            logger.info("Loaded config from {}", path)
        except FileNotFoundError:
            logger.info("No config file found at {}, starting fresh", path)

        return Config(global_config)

    def _get_config(self, id: int, category: str) -> dict:
        server_configs: dict = self.global_config.get(category, {})
        return server_configs.get(str(id), {})

    async def _set_config(self, id:int, category: str, configuration: dict):
        self.global_config[category][str(id)] = configuration
        async with aiofiles.open("config.json", mode="w") as f:
            await f.write(json.dumps(self.global_config, indent=2))
        logger.debug("Saved config: {}[{}]", category, id)

    def has_server_config(self, id: int) -> bool:
        return "servers" in self.global_config and id in self.global_config["servers"]

    def get_server_config(self, id: int) -> dict:
        return self._get_config(id, "servers")

    async def set_server_config(self, id: int, configuration: dict):
        return await self._set_config(id, "servers", configuration)
    
    def has_user_config(self, id: int) -> bool:
        return "users" in self.global_config and id in self.global_config["users"]

    def get_user_config(self, id: int) -> dict:
        return self._get_config(id, "users")

    async def set_user_config(self, id: int, configuration: dict):
        return await self._set_config(id, "users", configuration)

    # --- Server Authorization ---

    def get_authorized_servers(self) -> list[int] | None:
        """Return list of authorized guild IDs, or None if auth not active."""
        val = self.global_config.get("authorized_servers")
        return val if isinstance(val, list) else None

    async def set_authorized_servers(self, server_ids: list[int]):
        self.global_config["authorized_servers"] = server_ids
        await self._save()

    def is_server_authorized(self, guild_id: int) -> bool:
        """True if auth key is absent (backward compat) or guild is in the list."""
        servers = self.get_authorized_servers()
        if servers is None:
            return True
        return guild_id in servers

    def get_unauthorized_mode(self) -> str:
        return self.global_config.get("unauthorized_mode", "ignore")

    async def set_unauthorized_mode(self, mode: str):
        self.global_config["unauthorized_mode"] = mode
        await self._save()

    def get_polite_declined(self) -> list[int]:
        return self.global_config.get("polite_declined", [])

    async def add_polite_declined(self, guild_id: int):
        declined = self.get_polite_declined()
        if guild_id not in declined:
            declined.append(guild_id)
            self.global_config["polite_declined"] = declined
            await self._save()

    async def clear_polite_declined(self):
        self.global_config["polite_declined"] = []
        await self._save()

    async def _save(self):
        async with aiofiles.open("config.json", mode="w") as f:
            await f.write(json.dumps(self.global_config, indent=2))
