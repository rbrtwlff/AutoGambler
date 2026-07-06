from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ConfigError(ValueError):
    """Raised when a game rule is missing or inconsistent."""


class PopulationConfig(BaseModel):
    total: int = Field(gt=0)
    neutral: int = Field(ge=0)


class FactionConfig(BaseModel):
    id: str
    start_population: int = Field(ge=0)


class PlayerConfig(BaseModel):
    id: str
    faction_id: str
    bot_profile_id: str


class RoleToggle(BaseModel):
    enabled: bool = False


class SpecialRolesConfig(BaseModel):
    saboteur: RoleToggle = Field(default_factory=RoleToggle)
    journalist: RoleToggle = Field(default_factory=RoleToggle)
    media_mogul: RoleToggle = Field(default_factory=RoleToggle)


class PropagandaSlotConfig(BaseModel):
    id: str
    label: str


class PropagandaTrackConfig(BaseModel):
    slots: list[PropagandaSlotConfig]


class LimitsConfig(BaseModel):
    max_sources_per_player: int = Field(ge=0)
    starting_hand_size: int = Field(ge=0)
    draft_pick_count: int = Field(ge=0)


class RoundConfig(BaseModel):
    max_rounds: int = Field(gt=0)
    phases: list[str]


class ActionTypeConfig(BaseModel):
    id: str
    label: str
    population_delta: int = 0
    requires_target_faction: bool = True


class VictoryConditionConfig(BaseModel):
    id: str
    type: Literal["faction_population_at_least", "highest_population_at_round_limit"]
    threshold: int | None = None


class GameConfig(BaseModel):
    game_id: str
    population: PopulationConfig
    factions: list[FactionConfig]
    players: list[PlayerConfig]
    special_roles: SpecialRolesConfig
    propaganda_track: PropagandaTrackConfig
    limits: LimitsConfig
    rounds: RoundConfig
    action_types: list[ActionTypeConfig]
    victory_conditions: list[VictoryConditionConfig]

    @model_validator(mode="after")
    def validate_references(self) -> GameConfig:
        faction_ids = [faction.id for faction in self.factions]
        if len(faction_ids) != len(set(faction_ids)):
            raise ConfigError("Faction ids must be unique.")
        player_ids = [player.id for player in self.players]
        if len(player_ids) != len(set(player_ids)):
            raise ConfigError("Player ids must be unique.")
        for player in self.players:
            if player.faction_id not in faction_ids:
                raise ConfigError(f"Player {player.id} references unknown faction {player.faction_id}.")
        start_total = self.population.neutral + sum(faction.start_population for faction in self.factions)
        if start_total != self.population.total:
            raise ConfigError(
                f"Starting population must equal total population: configured {start_total}, expected {self.population.total}."
            )
        phase_ids = set(self.rounds.phases)
        for required_phase in {"draft", "planning", "resolution", "upkeep"}:
            if required_phase not in phase_ids:
                raise ConfigError(f"Prototype engine requires phase {required_phase!r}; make it configurable before removing it.")
        for condition in self.victory_conditions:
            if condition.type == "faction_population_at_least" and condition.threshold is None:
                raise ConfigError(f"Victory condition {condition.id} needs a threshold.")
        return self


class CardConfig(BaseModel):
    id: str
    name: str
    tags: list[str] = Field(default_factory=list)
    action_type_id: str


class CardsConfig(BaseModel):
    cards: list[CardConfig]

    @model_validator(mode="after")
    def validate_cards(self) -> CardsConfig:
        card_ids = [card.id for card in self.cards]
        if len(card_ids) != len(set(card_ids)):
            raise ConfigError("Card ids must be unique.")
        return self


class BotProfileConfig(BaseModel):
    id: str
    weights: dict[str, float] = Field(default_factory=dict)


class BotsConfig(BaseModel):
    profiles: list[BotProfileConfig]

    @model_validator(mode="after")
    def validate_profiles(self) -> BotsConfig:
        profile_ids = [profile.id for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ConfigError("Bot profile ids must be unique.")
        return self


class AnalysisConfig(BaseModel):
    outputs_dir: str = "outputs"
    event_log_format: str = "jsonl"
    summary_format: str = "csv"
    chatgpt_package: dict[str, bool] = Field(default_factory=dict)


class ConfigBundle(BaseModel):
    game: GameConfig
    cards: CardsConfig
    bots: BotsConfig
    analysis: AnalysisConfig

    @model_validator(mode="after")
    def validate_cross_file_references(self) -> ConfigBundle:
        action_ids = {action.id for action in self.game.action_types}
        for card in self.cards.cards:
            if card.action_type_id not in action_ids:
                raise ConfigError(f"Card {card.id} references unknown action type {card.action_type_id}.")
        profile_ids = {profile.id for profile in self.bots.profiles}
        for player in self.game.players:
            if player.bot_profile_id not in profile_ids:
                raise ConfigError(f"Player {player.id} references unknown bot profile {player.bot_profile_id}.")
        return self


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a mapping.")
    return data


def load_config(config_dir: Path) -> ConfigBundle:
    return ConfigBundle(
        game=GameConfig.model_validate(_load_yaml(config_dir / "game.yaml")),
        cards=CardsConfig.model_validate(_load_yaml(config_dir / "cards.yaml")),
        bots=BotsConfig.model_validate(_load_yaml(config_dir / "bots.yaml")),
        analysis=AnalysisConfig.model_validate(_load_yaml(config_dir / "analysis.yaml")),
    )

