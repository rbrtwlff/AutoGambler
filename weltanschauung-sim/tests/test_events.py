from __future__ import annotations

import json
from pathlib import Path

from wsim.config import load_cards_config, load_rules_config
from wsim.core.events import EventBus, EventType
from wsim.engine import create_initial_state


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def test_events_are_stored_in_order() -> None:
    bus = EventBus(run_id="run-1", game_id="game-1")

    bus.emit(EventType.GAME_STARTED, {"step": 1})
    bus.emit(EventType.ROUND_STARTED, {"step": 2})
    bus.emit(EventType.PHASE_STARTED, {"step": 3})

    assert [event.event_type for event in bus.event_log.events] == [
        EventType.GAME_STARTED,
        EventType.ROUND_STARTED,
        EventType.PHASE_STARTED,
    ]


def test_event_index_increments_correctly() -> None:
    bus = EventBus(run_id="run-1", game_id="game-1")

    first = bus.emit("game_started", {})
    second = bus.emit("warning", {})

    assert first.event_index == 0
    assert second.event_index == 1


def test_event_payload_is_preserved() -> None:
    bus = EventBus(run_id="run-1", game_id="game-1")
    payload = {"player_id": "P1", "nested": {"value": 7}}

    event = bus.emit(EventType.CARD_DRAWN, payload)

    assert event.payload == payload
    assert bus.export_events_as_dicts()[0]["payload"] == payload


def test_jsonl_export_works(tmp_path: Path) -> None:
    bus = EventBus(run_id="run-1", game_id="game-1")
    bus.emit(EventType.GAME_STARTED, {"seed": 123})
    bus.emit(EventType.WARNING, {"message": "example"})
    path = tmp_path / "events.jsonl"

    exported_path = bus.export_jsonl(path)
    lines = exported_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "game_started"
    assert json.loads(lines[1])["payload"] == {"message": "example"}


def test_initial_state_event_log_contains_game_started() -> None:
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    state = create_initial_state(rules, cards, seed=123)

    event_types = [event["event_type"] for event in state.export_events_as_dicts()]

    assert event_types[0] == "game_started"
    assert "initial_state_created" in event_types

