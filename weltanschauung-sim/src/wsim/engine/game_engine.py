from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from wsim.core.events import EventBus, EventType
from wsim.core.models import CardConfig, GameConfig
from wsim.core.state import GameState
from wsim.engine.setup import create_initial_state


class GameResult(BaseModel):
    game_id: str
    seed: int
    rounds_played: int
    ended_by: Literal["victory", "max_rounds"]
    winner_type: str | None = None
    winner_player: str | None = None
    winner_faction: str | None = None
    final_populations: dict[str, int]
    event_count: int


@dataclass
class PhaseContext:
    config: GameConfig
    cards: list[CardConfig]
    state: GameState
    event_bus: EventBus
    phase_name: str


class GameEngine:
    def __init__(self, config: GameConfig, cards: list[CardConfig], seed: int) -> None:
        self.config = config
        self.cards = cards
        self.seed = seed
        self.state = create_initial_state(config, cards, seed=seed)
        self.event_bus = EventBus(
            run_id=f"{config.game_id}-{seed}",
            game_id=config.game_id,
            round_number=self.state.round.round_number,
            phase=self.state.round.phase,
            event_log=self.state.event_log,
        )
        self.ended_by: Literal["victory", "max_rounds"] | None = None
        self.winner_type: str | None = None
        self.winner_player: str | None = None
        self.winner_faction: str | None = None

    def run_game(self) -> GameResult:
        while self.ended_by is None and self.state.round.round_number < self.config.round_flow.max_rounds:
            self.run_round()

        if self.ended_by is None:
            self.ended_by = "max_rounds"
            self.event_bus.set_context(round_number=self.state.round.round_number, phase=self.state.round.phase)
            self.event_bus.emit(
                EventType.GAME_ENDED,
                {
                    "ended_by": self.ended_by,
                    "max_rounds": self.config.round_flow.max_rounds,
                },
            )

        return self._build_result()

    def run_round(self) -> None:
        if self.ended_by is not None:
            return

        self.state.round.round_number += 1
        self.state.round.phase = "start_round"
        self.event_bus.set_context(round_number=self.state.round.round_number, phase=self.state.round.phase)
        self.event_bus.emit(
            EventType.ROUND_STARTED,
            {
                "round": self.state.round.round_number,
                "start_player_id": self.state.round.start_player_id,
            },
        )

        for phase_name in self.config.round_flow.phases:
            if self.ended_by is not None:
                break
            self.run_phase(phase_name)

    def run_phase(self, phase_name: str) -> None:
        self.state.round.phase = phase_name
        self.event_bus.set_context(round_number=self.state.round.round_number, phase=phase_name)
        self.event_bus.emit(EventType.PHASE_STARTED, {"phase": phase_name})

        context = PhaseContext(
            config=self.config,
            cards=self.cards,
            state=self.state,
            event_bus=self.event_bus,
            phase_name=phase_name,
        )
        handler = getattr(self, f"_phase_{phase_name}", self._phase_stub)
        handler(context)

    def _phase_start_round(self, context: PhaseContext) -> None:
        context.event_bus.emit(
            EventType.WARNING,
            {
                "phase": context.phase_name,
                "message": "Stub phase: start-of-round mechanics are not implemented yet.",
            },
        )

    def _phase_victory_check(self, context: PhaseContext) -> None:
        context.event_bus.emit(
            EventType.VICTORY_CHECKED,
            {
                "winner_type": None,
                "winner_player": None,
                "winner_faction": None,
                "message": "Stub victory check: no victory conditions are implemented yet.",
            },
        )

    def _phase_stub(self, context: PhaseContext) -> None:
        context.event_bus.emit(
            EventType.WARNING,
            {
                "phase": context.phase_name,
                "message": f"Stub phase: {context.phase_name} is not implemented yet.",
            },
        )

    def _build_result(self) -> GameResult:
        return GameResult(
            game_id=self.config.game_id,
            seed=self.seed,
            rounds_played=self.state.round.round_number,
            ended_by=self.ended_by or "max_rounds",
            winner_type=self.winner_type,
            winner_player=self.winner_player,
            winner_faction=self.winner_faction,
            final_populations={key: faction.population for key, faction in self.state.factions.items()},
            event_count=len(self.state.event_log.events),
        )

