from __future__ import annotations

from wsim.config import ConfigError
from wsim.core.models import BotConfig
from wsim.bots.base import BaseBot
from wsim.bots.heuristic_bot import DeceptiveBot, HeuristicBot, LoyalistBot, SaboteurBot
from wsim.bots.random_bot import RandomBot


class BotFactory:
    def create(self, config: BotConfig) -> BaseBot:
        if config.type == "random":
            return RandomBot(config)
        if config.type == "heuristic":
            return HeuristicBot(config)
        if config.type == "loyalist":
            return LoyalistBot(config)
        if config.type == "deceptive":
            return DeceptiveBot(config)
        if config.type == "saboteur":
            return SaboteurBot(config)
        raise ConfigError(f"Unknown bot type: {config.type}")

    def create_all(self, configs: list[BotConfig]) -> dict[str, BaseBot]:
        bots: dict[str, BaseBot] = {}
        for config in configs:
            if config.player_id is None:
                raise ConfigError(f"Bot {config.id} must define player_id.")
            bots[config.player_id] = self.create(config)
        return bots
