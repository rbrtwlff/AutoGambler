from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class FactionConfig(BaseModel):
    id: str
    name: str | None = None
    start_population: int = Field(ge=0)
    eliminated_at_start: bool = False


class PopulationConfig(BaseModel):
    total_population: int = Field(gt=0)
    neutral_start: int = Field(ge=0)


class PlayerConfig(BaseModel):
    id: str
    seat: int = Field(ge=0)
    faction_id: str


class RoleConfig(BaseModel):
    start_player: Literal["random"] | str = "random"
    first_journalist: Literal["left_of_start_player"] | str = "left_of_start_player"
    first_media_mogul: Literal["normal_rules"] | str = "normal_rules"
    saboteur: "SaboteurConfig" = Field(default_factory=lambda: SaboteurConfig())


class SaboteurConfig(BaseModel):
    enabled: bool = False
    count: int = Field(default=0, ge=0)


class PropagandaConfig(BaseModel):
    slots: int = Field(gt=0)
    overflow: Literal["remove_oldest"] | str = "remove_oldest"
    media_mogul_card_source: Literal["hand"] = "hand"
    media_mogul_card_types: list[Literal["propaganda", "hybrid"]] = Field(default_factory=lambda: ["propaganda", "hybrid"])


class RoundFlowConfig(BaseModel):
    phases: list[str] = Field(min_length=1)
    max_rounds: int = Field(gt=0)


class DraftConfig(BaseModel):
    enabled: bool = True
    starting_hand_size: int = Field(ge=0)
    hidden_research_orders: int = Field(ge=0)
    max_sources: int = Field(ge=0)
    draw_count: int = Field(default=0, ge=0)
    pick_count: int = Field(default=0, ge=0)
    pass_count: int = Field(default=0, ge=0)
    last_player_discard_count: int = Field(default=0, ge=0)
    direction: Literal["clockwise", "counterclockwise"] = "clockwise"


class SaboteurWinConditionConfig(BaseModel):
    type: Literal[
        "total_population_zero",
        "all_factions_zero",
        "destroyed_population_at_least",
        "factions_eliminated_at_least",
    ]
    threshold: int | None = None


class VictoryConfig(BaseModel):
    check_timing: list[Literal["after_population_change", "end_of_round", "game_end"]] = Field(
        default_factory=lambda: ["game_end"]
    )
    faction_win_mode: Literal["highest_population_at_game_end"] = "highest_population_at_game_end"
    tie_breakers: Literal["shared_win", "no_winner", "configured_order"] = "shared_win"
    saboteur_win_conditions: list[SaboteurWinConditionConfig] = Field(default_factory=list)


class AnalyticsConfig(BaseModel):
    outputs_dir: str = "outputs/runs"
    event_log_format: Literal["jsonl"] | str = "jsonl"


class CardConfig(BaseModel):
    id: str
    name: str
    type: Literal["action", "propaganda", "hybrid", "source", "research_order"]
    faction: str | None = None
    strength: int = 0
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    action_effects: list[dict[str, Any]] = Field(default_factory=list)
    propaganda_effects: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class BotConfig(BaseModel):
    id: str
    player_id: str | None = None
    type: Literal["random"] = "random"
    name: str | None = None
    weights: dict[str, float] = Field(default_factory=dict)


class GameConfig(BaseModel):
    game_id: str
    player_count: int = Field(ge=2)
    factions: list[FactionConfig]
    population: PopulationConfig
    players: list[PlayerConfig]
    roles: RoleConfig
    propaganda: PropagandaConfig
    round_flow: RoundFlowConfig
    draft: DraftConfig
    victory: VictoryConfig
    analytics: AnalyticsConfig
    cards: list[CardConfig] = Field(default_factory=list)
    bots: list[BotConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_game_config(self) -> GameConfig:
        faction_ids = [faction.id for faction in self.factions]
        if len(faction_ids) != len(set(faction_ids)):
            raise ValueError("Faction ids must be unique.")

        start_population = sum(faction.start_population for faction in self.factions)
        configured_total = start_population + self.population.neutral_start
        if configured_total != self.population.total_population:
            raise ValueError(
                "Starting faction population plus neutral_start must equal "
                f"total_population: {configured_total} != {self.population.total_population}."
            )

        if len(self.players) != self.player_count:
            raise ValueError(f"Expected {self.player_count} players, got {len(self.players)}.")

        player_ids = [player.id for player in self.players]
        if len(player_ids) != len(set(player_ids)):
            raise ValueError("Player ids must be unique.")

        known_factions = set(faction_ids)
        for player in self.players:
            if player.faction_id not in known_factions:
                raise ValueError(f"Player {player.id} references unknown faction {player.faction_id}.")

        return self
