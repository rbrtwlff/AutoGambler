from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from wsim.core.models import BotConfig, CardConfig, GameConfig


class ConfigError(ValueError):
    """Raised when a YAML config cannot be loaded or validated."""


def load_yaml(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")
    if not config_path.is_file():
        raise ConfigError(f"Config path is not a file: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc

    if data is None:
        raise ConfigError(f"Config file is empty: {config_path}")
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping/object: {config_path}")
    return data


def load_rules_config(path: str | Path) -> GameConfig:
    data = load_yaml(path)
    try:
        return GameConfig.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise ConfigError(f"Invalid rules config {Path(path)}: {details}") from exc


def load_cards_config(path: str | Path, rules_config: GameConfig | None = None) -> list[CardConfig]:
    data = load_yaml(path)
    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list):
        raise ConfigError(f"Cards config {Path(path)} must contain a 'cards' list.")

    try:
        cards = [CardConfig.model_validate(item) for item in raw_cards]
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise ConfigError(f"Invalid cards config {Path(path)}: {details}") from exc

    card_ids = [card.id for card in cards]
    duplicates = sorted({card_id for card_id in card_ids if card_ids.count(card_id) > 1})
    if duplicates:
        raise ConfigError(f"Card ids must be unique. Duplicates: {', '.join(duplicates)}")

    if rules_config is not None:
        known_factions = {faction.id for faction in rules_config.factions}
        for card in cards:
            if card.faction in (None, "", "neutral"):
                continue
            if card.faction not in known_factions:
                raise ConfigError(f"Card {card.id} references unknown faction {card.faction}.")

    from wsim.engine.effects import validate_card_effects

    validate_card_effects(cards)
    return cards


def load_bots_config(path: str | Path, rules_config: GameConfig | None = None) -> list[BotConfig]:
    data = load_yaml(path)
    raw_bots = data.get("bots")
    if not isinstance(raw_bots, list):
        raise ConfigError(f"Bots config {Path(path)} must contain a 'bots' list.")

    try:
        bots = [BotConfig.model_validate(item) for item in raw_bots]
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise ConfigError(f"Invalid bots config {Path(path)}: {details}") from exc

    bot_ids = [bot.id for bot in bots]
    duplicates = sorted({bot_id for bot_id in bot_ids if bot_ids.count(bot_id) > 1})
    if duplicates:
        raise ConfigError(f"Bot ids must be unique. Duplicates: {', '.join(duplicates)}")

    if rules_config is not None:
        player_ids = {player.id for player in rules_config.players}
        for bot in bots:
            if bot.player_id is not None and bot.player_id not in player_ids:
                raise ConfigError(f"Bot {bot.id} references unknown player {bot.player_id}.")

    return bots
