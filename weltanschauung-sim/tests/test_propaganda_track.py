from __future__ import annotations

from pathlib import Path

import yaml

from wsim.config import load_cards_config, load_rules_config
from wsim.core.state import PropagandaTrackState
from wsim.engine import GameEngine


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _rules_with_slots(tmp_path: Path, slots: int):
    with BASE_RULES.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    data["propaganda"]["slots"] = slots
    path = tmp_path / f"rules_{slots}_slots.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_rules_config(path)


def _engine(tmp_path: Path, slots: int = 3) -> GameEngine:
    rules = _rules_with_slots(tmp_path, slots)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    return GameEngine(rules, cards, seed=123)


def _remove_card_from_locations(engine: GameEngine, card_id: str) -> None:
    for pile in [
        engine.state.deck.draw_pile,
        engine.state.deck.discard_pile,
        engine.state.deck.research_order_pool,
        engine.state.deck.disabled_cards,
    ]:
        while card_id in pile:
            pile.remove(card_id)
    for player in engine.state.players.values():
        for zone in [player.hand, player.hidden_research_orders, player.sources]:
            while card_id in zone:
                zone.remove(card_id)
    engine.state.propaganda_track.slots = [
        None if current_id == card_id else current_id for current_id in engine.state.propaganda_track.slots
    ]


def _set_hand(engine: GameEngine, player_id: str, card_ids: list[str]) -> None:
    player = engine.state.players[player_id]
    for card_id in player.hand:
        engine.state.deck.discard_pile.append(card_id)
    for card_id in card_ids:
        _remove_card_from_locations(engine, card_id)
    player.hand = list(card_ids)


def test_card_is_placed_in_slot() -> None:
    track = PropagandaTrackState(slots=[None, None, None], overflow="remove_oldest")

    removed = track.place_card("propaganda_01")

    assert removed is None
    assert track.get_slots() == ["propaganda_01", None, None]
    assert track.get_card_at_slot(0) == "propaganda_01"


def test_full_track_removes_oldest() -> None:
    track = PropagandaTrackState(slots=["oldest", "middle", "newest"], overflow="remove_oldest")

    removed = track.place_card("incoming")

    assert removed == "oldest"
    assert track.get_slots() == ["middle", "newest", "incoming"]


def test_three_and_four_slots_work(tmp_path: Path) -> None:
    three_slot_engine = _engine(tmp_path, slots=3)
    four_slot_engine = _engine(tmp_path, slots=4)

    assert len(three_slot_engine.state.propaganda_track.get_slots()) == 3
    assert len(four_slot_engine.state.propaganda_track.get_slots()) == 4


def test_slot_positions_are_stable() -> None:
    track = PropagandaTrackState(slots=[None, None, None], overflow="remove_oldest")

    track.place_card("a")
    track.place_card("b")
    track.place_card("c")
    track.remove_card("b")
    track.place_card("d")

    assert track.get_card_at_slot(0) == "a"
    assert track.get_card_at_slot(1) == "c"
    assert track.get_card_at_slot(2) == "d"
    assert track.serialize() == {"slots": ["a", "c", "d"], "overflow": "remove_oldest"}


def test_media_mogul_phase_logs_placement_event(tmp_path: Path) -> None:
    engine = _engine(tmp_path, slots=3)
    media_mogul_id = engine.state.round.media_mogul_player_id
    _set_hand(engine, media_mogul_id, ["propaganda_01"])

    engine.run_phase("media_mogul_phase")
    events = engine.state.export_events_as_dicts()

    assert engine.state.propaganda_track.get_slots()[0] == "propaganda_01"
    assert any(event["event_type"] == "propaganda_placed" for event in events)


def test_media_mogul_phase_logs_removed_event_on_overflow(tmp_path: Path) -> None:
    engine = _engine(tmp_path, slots=3)
    media_mogul_id = engine.state.round.media_mogul_player_id
    engine.state.propaganda_track.slots = ["oldest", "middle", "newest"]
    _set_hand(engine, media_mogul_id, ["propaganda_01"])

    engine.run_phase("media_mogul_phase")
    events = engine.state.export_events_as_dicts()

    assert engine.state.propaganda_track.get_slots() == ["middle", "newest", "propaganda_01"]
    assert "oldest" in engine.state.deck.discard_pile
    assert any(event["event_type"] == "propaganda_removed" for event in events)
    assert any(event["event_type"] == "propaganda_placed" for event in events)


def test_media_mogul_phase_warns_without_suitable_card(tmp_path: Path) -> None:
    engine = _engine(tmp_path, slots=3)
    media_mogul_id = engine.state.round.media_mogul_player_id
    _set_hand(engine, media_mogul_id, ["source_01"])

    engine.run_phase("media_mogul_phase")
    events = engine.state.export_events_as_dicts()

    assert any(
        event["event_type"] == "warning"
        and "no suitable propaganda or hybrid card" in event["payload"]["message"]
        for event in events
    )
