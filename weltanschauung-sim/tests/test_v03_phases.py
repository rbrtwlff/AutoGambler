from __future__ import annotations

from pathlib import Path

from wsim.config import load_cards_config, load_rules_config
from wsim.core.events import EventType
from wsim.core.state import PropagandaTrackState
from wsim.engine.game_engine import GameEngine
from wsim.engine.invariants import validate_game_state


RULES_V0_3 = Path("configs/rules/rules_v0_3.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _engine(seed: int = 123) -> GameEngine:
    rules = load_rules_config(RULES_V0_3)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    return GameEngine(rules, cards, seed=seed)


def _event_types(engine: GameEngine) -> list[str]:
    return [event["event_type"] for event in engine.state.export_events_as_dicts()]


def _move_to_zone(engine: GameEngine, card_id: str, zone: list[str]) -> None:
    for pile in [
        engine.state.deck.draw_pile,
        engine.state.deck.discard_pile,
        engine.state.deck.removed_from_game,
        engine.state.deck.research_order_pool,
        engine.state.draft_pool,
        engine.state.journalist_pool,
        engine.state.media_mogul_pool,
        engine.state.urn,
        engine.state.world_history_row,
    ]:
        if card_id in pile:
            pile.remove(card_id)
    for player in engine.state.players.values():
        for pile in [player.hand, player.hidden_research_orders, player.sources]:
            if card_id in pile:
                pile.remove(card_id)
    engine.state.propaganda_track.slots = [
        None if current == card_id else current
        for current in engine.state.propaganda_track.slots
    ]
    zone.append(card_id)


def test_each_v03_phase_runs_individually() -> None:
    engine = _engine()

    for phase in engine.config.round_flow.phases:
        engine.run_phase(phase)
        validate_game_state(engine.config, engine.state, engine.cards)

    event_types = _event_types(engine)
    assert EventType.ROUND_START_SNAPSHOT.value in event_types
    assert EventType.JOURNALIST_CHECKED.value in event_types
    assert EventType.MEDIA_MOGUL_CHANGED.value in event_types
    assert EventType.DRAFT_FINISHED.value in event_types
    assert EventType.DISCUSSION_HELD.value in event_types
    assert EventType.URN_SHUFFLED.value in event_types
    assert EventType.WORLD_HISTORY_POWER_CALCULATED.value in event_types
    assert EventType.VICTORY_CHECKED.value in event_types
    assert EventType.RESEARCH_ASSIGNMENTS_CHECKED.value in event_types
    assert EventType.ROUND_ENDED.value in event_types


def test_full_v03_round_runs_in_configured_order() -> None:
    engine = _engine()
    engine.run_round()

    phase_events = [
        event["payload"]["phase"]
        for event in engine.state.export_events_as_dicts()
        if event["event_type"] == EventType.PHASE_STARTED.value
    ]
    assert phase_events == engine.config.round_flow.phases
    assert engine.state.total_population() == 100
    validate_game_state(engine.config, engine.state, engine.cards)


def test_full_v03_game_runs_until_max_rounds_or_victory() -> None:
    engine = _engine()
    result = engine.run_game()

    assert result.rounds_played <= engine.config.round_flow.max_rounds
    assert result.ended_by in {"max_rounds", "victory"}
    assert engine.state.total_population() == 100


def test_v03_does_not_use_legacy_action_phases() -> None:
    engine = _engine()

    assert "planning" not in engine.config.round_flow.phases
    assert "reveal" not in engine.config.round_flow.phases
    assert "action_resolution" not in engine.config.round_flow.phases


def test_v03_propaganda_slot_one_is_newest_and_slot_four_is_displaced() -> None:
    track = PropagandaTrackState(slots=[None, None, None, None], overflow="remove_oldest")

    assert track.place_card_newest("p1") is None
    assert track.get_slots() == ["p1", None, None, None]
    assert track.place_card_newest("p2") is None
    assert track.get_slots() == ["p2", "p1", None, None]
    assert track.place_card_newest("p3") is None
    assert track.place_card_newest("p4") is None
    assert track.place_card_newest("p5") == "p1"
    assert track.get_slots() == ["p5", "p4", "p3", "p2"]


def test_v03_urn_anonymizes_and_moves_cards() -> None:
    engine = _engine()
    before_hand_total = sum(len(player.hand) for player in engine.state.players.values())

    engine.run_phase("urn")

    assert len(engine.state.urn) > 0
    assert sum(len(player.hand) for player in engine.state.players.values()) < before_hand_total
    assert EventType.URN_SHUFFLED.value in _event_types(engine)
    validate_game_state(engine.config, engine.state, engine.cards)


def test_v03_world_history_calculates_base_power() -> None:
    engine = _engine()
    for card_id in ["red_action_03", "red_action_04", "black_action_01"]:
        _move_to_zone(engine, card_id, engine.state.urn)

    engine.run_phase("world_history_and_combat")

    power_events = [
        event for event in engine.state.export_events_as_dicts()
        if event["event_type"] == EventType.WORLD_HISTORY_POWER_CALCULATED.value
    ]
    payload = power_events[-1]["payload"]
    assert payload["base_power"]["red"] == 4
    assert payload["base_power"]["black"] == 1
    assert engine.state.world_history_row == ["red_action_03", "red_action_04", "black_action_01"]


def test_v03_propaganda_power_requires_same_faction_in_world_history() -> None:
    engine = _engine()
    _move_to_zone(engine, "red_hybrid_04", engine.state.propaganda_track.slots)
    _move_to_zone(engine, "black_action_03", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["red_hybrid_04", "black_action_03", None, None]
    _move_to_zone(engine, "red_action_03", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    payload = [
        event for event in engine.state.export_events_as_dicts()
        if event["event_type"] == EventType.WORLD_HISTORY_POWER_CALCULATED.value
    ][-1]["payload"]
    assert payload["propaganda_power"]["red"] > 0
    assert payload["propaganda_power"]["black"] == 0


def test_v03_combat_is_applied_simultaneously_and_keeps_population_total() -> None:
    engine = _engine()
    for card_id in ["red_hybrid_04", "black_action_01"]:
        _move_to_zone(engine, card_id, engine.state.urn)
    before_black = engine.state.factions["black"].population

    engine.run_phase("world_history_and_combat")

    assert engine.state.factions["black"].population <= before_black
    assert engine.state.total_population() == 100
    assert EventType.COMBAT_RESOLVED.value in _event_types(engine)


def test_v03_victory_checks_order_before_lower_tiers() -> None:
    engine = _engine()
    engine.state.factions["red"].population = 0
    engine.state.factions["black"].population = 0
    engine.state.factions["yellow"].population = 0
    engine.state.factions["green"].population = 20
    engine.state.neutral_population = 80

    engine.run_phase("victory_check")

    assert engine.ended_by == "victory"
    assert engine.winning_condition == "hegemony"
    assert engine.winner_faction == "green"
