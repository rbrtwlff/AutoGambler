from __future__ import annotations

import random
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from wsim.core.events import EventBus, EventLog, EventType
from wsim.core.models import CardConfig, DeckConfig


class GameRuleError(RuntimeError):
    """Raised when a configured game rule cannot be fulfilled."""

    def __init__(self, message: str, *, game_id: str | None = None, round_number: int | None = None, phase: str | None = None) -> None:
        self.game_id = game_id
        self.round_number = round_number
        self.phase = phase
        super().__init__(message)

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

    def random_float(self) -> float:
        return self._random.random()


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


class CardInstance(BaseModel):
    instance_id: str
    card_id: str
    name: str
    type: Literal["action", "propaganda", "hybrid", "source", "research_order"]
    faction: str | None = None
    strength: int = 0
    tags: list[str] = Field(default_factory=list)


class DeckState(BaseModel):
    draw_pile: list[str]
    discard_pile: list[str] = Field(default_factory=list)
    removed_from_game: list[str] = Field(default_factory=list)
    research_order_pool: list[str] = Field(default_factory=list)
    disabled_cards: list[str] = Field(default_factory=list)
    card_instances: dict[str, CardInstance] = Field(default_factory=dict)

    def logical_card_id(self, instance_id: str) -> str:
        return self.card_instances.get(instance_id, CardInstance(instance_id=instance_id, card_id=instance_id, name=instance_id, type="action")).card_id

    def draw_cards(
        self,
        count: int,
        *,
        config: DeckConfig,
        rng: GameRng,
        event_bus: EventBus,
        reason: str,
        player_id: str | None = None,
        destination: str = "hand",
        game_id: str | None = None,
        round_number: int | None = None,
        phase: str | None = None,
    ) -> list[str]:
        drawn: list[str] = []
        for _ in range(count):
            if not self.draw_pile:
                event_bus.emit(
                    EventType.DRAW_PILE_EMPTY,
                    {
                        "reason": reason,
                        "player_id": player_id,
                        "discard_count": len(self.discard_pile),
                    },
                )
                if self.discard_pile and config.reshuffle_discard_when_empty:
                    self.reshuffle_discard_into_draw_pile(config=config, rng=rng, event_bus=event_bus, reason=reason)
            if not self.draw_pile:
                missing = count - len(drawn)
                event_bus.emit(
                    EventType.NOT_ENOUGH_CARDS_TO_DRAW,
                    {
                        "requested": count,
                        "drawn": len(drawn),
                        "missing": missing,
                        "reason": reason,
                        "policy": config.when_not_enough_cards,
                        "player_id": player_id,
                    },
                )
                if config.when_not_enough_cards == "error":
                    raise GameRuleError(
                        f"Not enough cards to draw: requested={count}, drawn={len(drawn)}, reason={reason}.",
                        game_id=game_id,
                        round_number=round_number,
                        phase=phase,
                    )
                break
            instance_id = self.draw_pile.pop(0)
            drawn.append(instance_id)
            event_bus.emit(
                EventType.CARD_DRAWN,
                {
                    "player_id": player_id,
                    "card_id": self.logical_card_id(instance_id),
                    "instance_id": instance_id,
                    "destination": destination,
                    "reason": reason,
                },
            )
        return drawn

    def discard_card(self, instance_id: str, *, event_bus: EventBus, reason: str, player_id: str | None = None) -> None:
        self.discard_pile.append(instance_id)
        event_bus.emit(
            EventType.CARD_DISCARDED,
            {
                "player_id": player_id,
                "card_id": self.logical_card_id(instance_id),
                "instance_id": instance_id,
                "reason": reason,
            },
        )

    def remove_from_game(self, instance_id: str, *, event_bus: EventBus, reason: str, player_id: str | None = None) -> None:
        self.removed_from_game.append(instance_id)
        event_bus.emit(
            EventType.CARD_REMOVED_FROM_GAME,
            {
                "player_id": player_id,
                "card_id": self.logical_card_id(instance_id),
                "instance_id": instance_id,
                "reason": reason,
                "destination": "removed_from_game",
            },
        )

    def reshuffle_discard_into_draw_pile(
        self,
        *,
        config: DeckConfig,
        rng: GameRng,
        event_bus: EventBus,
        reason: str,
    ) -> None:
        moved_count = len(self.discard_pile)
        self.draw_pile = list(self.discard_pile)
        self.discard_pile.clear()
        rng.shuffle(self.draw_pile)
        if config.log_reshuffle_events:
            event_bus.emit(
                EventType.DECK_RESHUFFLED,
                {
                    "reason": reason,
                    "moved_count": moved_count,
                    "draw_pile_count": len(self.draw_pile),
                    "discard_pile_count": len(self.discard_pile),
                },
            )

    def cards_remaining(self) -> int:
        return len(self.draw_pile)

    def discard_count(self) -> int:
        return len(self.discard_pile)


class PropagandaTrackState(BaseModel):
    slots: list[str | None]
    overflow: str

    def place_card(self, card: str) -> str | None:
        if None in self.slots:
            self.slots[self.slots.index(None)] = card
            return None
        if self.overflow != "remove_oldest":
            raise ValueError(f"Unsupported propaganda overflow mode: {self.overflow}")
        removed_card = self.slots.pop(0)
        self.slots.append(card)
        return removed_card

    def remove_card(self, card_id: str) -> str | None:
        if card_id not in self.slots:
            return None
        self.slots.remove(card_id)
        self.slots.append(None)
        return card_id

    def get_slots(self) -> list[str | None]:
        return list(self.slots)

    def get_card_at_slot(self, position: int) -> str | None:
        return self.slots[position]

    def serialize(self) -> dict[str, Any]:
        return {"slots": self.get_slots(), "overflow": self.overflow}


class RoundState(BaseModel):
    round_number: int = 0
    phase: str = "setup"
    start_player_id: str
    journalist_player_id: str
    media_mogul_player_id: str
    role_assignment_notes: list[str] = Field(default_factory=list)


class PlannedAction(BaseModel):
    player_id: str
    committed_card_ids: list[str]
    action_type: Literal["support", "attack"]
    acting_faction_id: str
    target_faction_id: str

    @property
    def initiative_count(self) -> int:
        return len(self.committed_card_ids)


class RevealedAction(BaseModel):
    player_id: str
    committed_card_ids: list[str]
    action_type: Literal["support", "attack"]
    acting_faction_id: str
    target_faction_id: str
    strength: int
    initiative_count: int
    reveal_order: int


class ResolvedAction(BaseModel):
    player_id: str
    action_type: Literal["support", "attack"]
    target_faction_id: str
    strength: int
    population_before: int
    population_after: int
    neutral_before: int
    neutral_after: int
    applied_delta: int


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
    planned_actions: dict[str, PlannedAction] = Field(default_factory=dict)
    revealed_actions: list[RevealedAction] = Field(default_factory=list)
    resolved_actions: list[ResolvedAction] = Field(default_factory=list)
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
            "propaganda_track": self.propaganda_track.serialize(),
            "round": self.round.model_dump(),
            "event_count": len(self.event_log.events),
        }

    def to_analysis_view(self) -> dict[str, Any]:
        data = self.model_dump()
        data["events"] = self.event_log.export_events_as_dicts()
        return data

    def export_events_as_dicts(self) -> list[dict[str, Any]]:
        return self.event_log.export_events_as_dicts()
