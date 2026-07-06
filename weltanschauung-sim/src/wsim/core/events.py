from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    GAME_STARTED = "game_started"
    INITIAL_STATE_CREATED = "initial_state_created"
    ROUND_STARTED = "round_started"
    PHASE_STARTED = "phase_started"
    DRAFT_STARTED = "draft_started"
    DRAFT_FINISHED = "draft_finished"
    CARD_DRAWN = "card_drawn"
    CARD_DRAFTED = "card_drafted"
    CARD_DISCARDED = "card_discarded"
    PROPAGANDA_PLACED = "propaganda_placed"
    PROPAGANDA_REMOVED = "propaganda_removed"
    ACTION_COMMITTED = "action_committed"
    ACTION_REVEALED = "action_revealed"
    ACTION_RESOLVED = "action_resolved"
    EFFECT_TRIGGERED = "effect_triggered"
    POPULATION_CHANGED = "population_changed"
    VICTORY_CHECKED = "victory_checked"
    GAME_ENDED = "game_ended"
    WARNING = "warning"


class GameEvent(BaseModel):
    run_id: str
    game_id: str
    round: int
    phase: str
    event_index: int
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["event_type"] = self.event_type.value
        return data


class EventLog(BaseModel):
    events: list[GameEvent] = Field(default_factory=list)

    def append(self, event: GameEvent) -> None:
        self.events.append(event)

    def export_events_as_dicts(self) -> list[dict[str, Any]]:
        return [event.as_dict() for event in self.events]

    def export_jsonl(self, path: str | Path) -> Path:
        export_path = Path(path)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8") as file:
            for event in self.export_events_as_dicts():
                file.write(json.dumps(event, ensure_ascii=True) + "\n")
        return export_path


class EventBus:
    def __init__(
        self,
        *,
        run_id: str,
        game_id: str,
        round_number: int = 0,
        phase: str = "setup",
        event_log: EventLog | None = None,
    ) -> None:
        self.run_id = run_id
        self.game_id = game_id
        self.round_number = round_number
        self.phase = phase
        self.event_log = event_log or EventLog()

    def set_context(self, *, round_number: int | None = None, phase: str | None = None) -> None:
        if round_number is not None:
            self.round_number = round_number
        if phase is not None:
            self.phase = phase

    def emit(self, event_type: EventType | str, payload: dict[str, Any] | None = None) -> GameEvent:
        parsed_event_type = EventType(event_type)
        event = GameEvent(
            run_id=self.run_id,
            game_id=self.game_id,
            round=self.round_number,
            phase=self.phase,
            event_index=len(self.event_log.events),
            event_type=parsed_event_type,
            payload=payload or {},
        )
        self.event_log.append(event)
        return event

    def export_events_as_dicts(self) -> list[dict[str, Any]]:
        return self.event_log.export_events_as_dicts()

    def export_jsonl(self, path: str | Path) -> Path:
        return self.event_log.export_jsonl(path)
