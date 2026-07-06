from __future__ import annotations

import random
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from wsim.core.events import EventLog

T = TypeVar("T")


class GameRng:
    """Central deterministic RNG wrapper for a game."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._random = random.Random(seed)

    def choice(self, items: list[str]) -> str:
        return self._random.choice(items)

    def choose(self, items: list[T]) -> T:
        return self._random.choice(items)

    def shuffle(self, items: list[str]) -> None:
        self._random.shuffle(items)

    def sample(self, items: list[str], count: int) -> list[str]:
        return self._random.sample(items, count)


class FactionState(BaseModel):
    id: str
    population: int
    eliminated: bool = False


class PlayerState(BaseModel):
    id: str
    seat: int
    public_faction_id: str
    secret_faction_id: str | None = None
    is_saboteur: bool = False
    roles: list[str] = Field(default_factory=list)
    hand: list[str] = Field(default_factory=list)
    hidden_research_orders: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    max_sources: int = Field(ge=0)


class DeckState(BaseModel):
    draw_pile: list[str]
    discard_pile: list[str] = Field(default_factory=list)
    research_order_pool: list[str] = Field(default_factory=list)
    disabled_cards: list[str] = Field(default_factory=list)


class PropagandaTrackState(BaseModel):
    slots: list[str | None]
    overflow: str


class RoundState(BaseModel):
    round_number: int = 0
    phase: str = "setup"
    start_player_id: str
    journalist_player_id: str
    media_mogul_player_id: str
    role_assignment_notes: list[str] = Field(default_factory=list)


class GameState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    seed: int
    rng: GameRng = Field(exclude=True)
    players: dict[str, PlayerState]
    factions: dict[str, FactionState]
    neutral_population: int
    deck: DeckState
    propaganda_track: PropagandaTrackState
    round: RoundState
    event_log: EventLog = Field(default_factory=EventLog)

    def total_population(self) -> int:
        return self.neutral_population + sum(faction.population for faction in self.factions.values())

    def to_public_view(self, player_id: str) -> dict[str, Any]:
        if player_id not in self.players:
            raise KeyError(f"Unknown player_id: {player_id}")

        players: dict[str, dict[str, Any]] = {}
        for current_id, player in self.players.items():
            if current_id == player_id:
                players[current_id] = player.model_dump()
            else:
                players[current_id] = {
                    "id": player.id,
                    "seat": player.seat,
                    "public_faction_id": player.public_faction_id,
                    "roles": list(player.roles),
                    "hand_size": len(player.hand),
                    "hidden_research_order_count": len(player.hidden_research_orders),
                    "source_count": len(player.sources),
                    "max_sources": player.max_sources,
                }

        return {
            "seed": self.seed,
            "viewer_player_id": player_id,
            "players": players,
            "factions": {key: value.model_dump() for key, value in self.factions.items()},
            "neutral_population": self.neutral_population,
            "deck": {
                "draw_pile_count": len(self.deck.draw_pile),
                "discard_pile_count": len(self.deck.discard_pile),
                "research_order_pool_count": len(self.deck.research_order_pool),
                "disabled_card_count": len(self.deck.disabled_cards),
            },
            "propaganda_track": self.propaganda_track.model_dump(),
            "round": self.round.model_dump(),
            "event_count": len(self.event_log.events),
        }

    def to_analysis_view(self) -> dict[str, Any]:
        data = self.model_dump()
        data["events"] = self.event_log.export_events_as_dicts()
        return data

    def export_events_as_dicts(self) -> list[dict[str, Any]]:
        return self.event_log.export_events_as_dicts()
