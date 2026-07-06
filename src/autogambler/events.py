from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Event:
    type: str
    round_number: int
    phase: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "created_at": self.created_at,
            "type": self.type,
            "round_number": self.round_number,
            "phase": self.phase,
            "payload": self.payload,
        }


class EventLog:
    def __init__(self) -> None:
        self._events: list[Event] = []

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def record(self, event_type: str, round_number: int, phase: str, **payload: Any) -> Event:
        event = Event(type=event_type, round_number=round_number, phase=phase, payload=payload)
        self._events.append(event)
        return event

