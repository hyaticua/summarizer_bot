import aiofiles
import json
from collections import defaultdict

class Config:
    def __init__(self, global_config: dict):
        self.global_config = global_config

    @staticmethod
    def try_init_from_file(path: str) -> "Config":
        global_config = defaultdict(dict)

        try:
            with open(path, "r") as f:
                global_config.update(json.loads(f.read()))
        except FileNotFoundError:
            pass

        return Config(global_config)

    def _get_config(self, id: int, category: str) -> dict:
        server_configs: dict = self.global_config.get(category, {})
        return server_configs.get(str(id), {})

    async def _set_config(self, id:int, category: str, configuration: dict):
        self.global_config[category][str(id)] = configuration
        async with aiofiles.open("config.json", mode="w") as f:
            await f.write(json.dumps(self.global_config, indent=2))

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
