from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlayerState:
    id: str
    faction_id: str
    bot_profile_id: str
    hand: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    secret_goal_id: str | None = None
    special_role: str | None = None


@dataclass
class PlannedAction:
    player_id: str
    action_type_id: str
    target_faction_id: str | None = None
    card_id: str | None = None


@dataclass
class GameState:
    round_number: int
    phase: str
    faction_population: dict[str, int]
    neutral_population: int
    players: dict[str, PlayerState]
    deck: list[str]
    discard: list[str] = field(default_factory=list)
    planned_actions: list[PlannedAction] = field(default_factory=list)
    winner_faction_id: str | None = None
    victory_condition_id: str | None = None

    def population_total(self) -> int:
        return self.neutral_population + sum(self.faction_population.values())


@dataclass(frozen=True)
class BotView:
    player_id: str
    own_faction_id: str
    own_hand: tuple[str, ...]
    own_sources: tuple[str, ...]
    public_faction_population: dict[str, int]
    neutral_population: int
    round_number: int
    phase: str

