from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from wsim.core.models import BotConfig, CardConfig, GameConfig, RulesV03Config


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
    if _is_v03_rules_config(data):
        return _load_v03_rules_config(data, Path(path))
    try:
        return GameConfig.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise ConfigError(f"Invalid rules config {Path(path)}: {details}") from exc


def _is_v03_rules_config(data: dict[str, Any]) -> bool:
    return isinstance(data.get("game"), dict) and isinstance(data.get("round_flow"), list)


def _load_v03_rules_config(data: dict[str, Any], path: Path) -> GameConfig:
    try:
        v03 = RulesV03Config.model_validate(data)
        return GameConfig.model_validate(_v03_to_game_config_data(v03))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise ConfigError(f"Invalid v0.3 rules config {path}: {details}") from exc


def _v03_to_game_config_data(v03: RulesV03Config) -> dict[str, Any]:
    faction_start = {faction_id: v03.population.start[faction_id] for faction_id in v03.factions}
    players = [
        {
            "id": f"P{index + 1}",
            "seat": index,
            "faction_id": v03.factions[index % len(v03.factions)],
        }
        for index in range(v03.game.player_count)
    ]
    journalist_options = []
    if "future_set" in v03.journalist_phase.options:
        journalist_options.append("place_one_on_top_of_deck")
    if "remove_propaganda" in v03.journalist_phase.options:
        journalist_options.append("remove_one_propaganda_card_from_game")
    if not journalist_options:
        journalist_options = ["discard_one"]

    return {
        "game_id": f"{v03.game.name}_{v03.game.version}".replace(".", "_"),
        "player_count": v03.game.player_count,
        "factions": [
            {
                "id": faction_id,
                "name": faction_id.title(),
                "start_population": faction_start[faction_id],
                "eliminated_at_start": False,
            }
            for faction_id in v03.factions
        ],
        "population": {
            "total_population": v03.population.total,
            "neutral_start": v03.population.start["neutral"],
        },
        "players": players,
        "roles": {
            "start_player": v03.game.start_player_mode,
            "first_journalist": v03.roles.journalist.initial_holder,
            "first_media_mogul": v03.roles.media_mogul.initial_holder,
            "saboteur": {
                "enabled": False,
                "count": 0,
            },
        },
        "propaganda": {
            "slots": v03.propaganda.slots,
            "overflow": "remove_oldest",
            "journalist_options": journalist_options,
            "media_mogul_draw_count": v03.media_mogul_phase.receives_cards,
            "media_mogul_choice_count": 1,
            "allowed_sources_for_propaganda": "both",
            "media_mogul_card_source": "hand",
            "media_mogul_card_types": ["propaganda", "hybrid"],
        },
        "round_flow": {
            "max_rounds": v03.game.max_rounds,
            "phases": v03.round_flow,
        },
        "draft": {
            "enabled": True,
            "starting_hand_size": v03.cards.starting_hand_size,
            "hidden_research_orders": v03.research_assignments.starting_assignments,
            "max_sources": v03.sources.max_sources,
            "draw_count": v03.draft.cards_seen_each_pick,
            "pick_count": v03.draft.cards_taken_each_pick,
            "pass_count": v03.draft.cards_passed_each_pick,
            "last_player_discard_count": 0,
            "direction": "clockwise",
        },
        "deck": {
            "reshuffle_discard_when_empty": v03.deck.reshuffle_discard_when_empty,
            "when_not_enough_cards": v03.deck.when_not_enough_cards,
            "log_reshuffle_events": True,
        },
        "victory": {
            "check_timing": ["end_of_round", "game_end"],
            "faction_win_mode": "highest_population_at_game_end",
            "tie_breakers": "shared_win",
            "saboteur_win_conditions": [],
        },
        "analytics": {},
        "quality": {},
        "cards": [],
        "bots": [],
        "v03": v03.model_dump(mode="json"),
    }


def load_cards_config(path: str | Path, rules_config: GameConfig | None = None) -> list[CardConfig]:
    data = load_yaml(path)
    raw_cards = _load_cards_data(path, data, seen=set())
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


def _load_cards_data(path: str | Path, data: dict[str, Any], seen: set[Path]) -> list[dict[str, Any]]:
    config_path = Path(path).resolve()
    if config_path in seen:
        raise ConfigError(f"Cards include cycle detected at {config_path}.")
    seen.add(config_path)

    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list):
        raise ConfigError(f"Cards config {Path(path)} must contain a 'cards' list.")

    merged_cards = list(raw_cards)
    includes = data.get("includes", data.get("include", []))
    if includes is None:
        includes = []
    if isinstance(includes, (str, Path)):
        includes = [includes]
    if not isinstance(includes, list):
        raise ConfigError(f"Cards config {Path(path)} includes must be a list.")

    for include in includes:
        include_path = Path(include)
        if not include_path.is_absolute():
            include_path = Path(path).parent / include_path
        include_data = load_yaml(include_path)
        merged_cards.extend(_load_cards_data(include_path, include_data, seen))
    return merged_cards


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
