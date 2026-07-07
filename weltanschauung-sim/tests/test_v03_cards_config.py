from __future__ import annotations

from collections import Counter
from pathlib import Path

from typer.testing import CliRunner

from wsim.cli import app
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.core.events import EventType
from wsim.core.state import GameRng
from wsim.engine.game_engine import GameEngine
from wsim.engine.setup import build_deck, create_initial_state


RULES_V03 = Path("configs/rules/rules_v0_3.yaml")
CARDS_V03 = Path("configs/cards/cards_v0_3.yaml")
BOTS = Path("configs/bots/bot_profiles.yaml")


def _put_research_order_on_player(engine: GameEngine, player_id: str, order_id: str) -> None:
    for player in engine.state.players.values():
        for card_id in list(player.hidden_research_orders):
            if card_id != order_id:
                engine.state.deck.research_order_pool.append(card_id)
        player.hidden_research_orders.clear()
    if order_id in engine.state.deck.research_order_pool:
        engine.state.deck.research_order_pool.remove(order_id)
    engine.state.players[player_id].hidden_research_orders = [order_id]


def _move_card_to_zone(engine: GameEngine, card_id: str, zone: list[str]) -> None:
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


def test_v03_cards_load_with_imported_faction_cards() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)

    faction_cards = [card for card in cards if "faction_card" in card.tags]
    research_orders = [card for card in cards if card.type == "research_order"]

    assert len(faction_cards) == 240
    assert len(research_orders) == 60
    assert {card.faction for card in faction_cards} == {"red", "black", "yellow", "green"}
    assert Counter(card.faction for card in faction_cards) == {"red": 60, "black": 60, "yellow": 60, "green": 60}
    assert Counter(card.points for card in research_orders) == {1: 36, 2: 18, 3: 6}
    assert all(card.category for card in research_orders)
    assert all(card.scope for card in research_orders)
    assert all(card.condition for card in research_orders)
    assert all(card.type == "hybrid" for card in faction_cards)
    assert all(card.count == 1 for card in faction_cards)
    assert all(card.rules_version == "0.3" for card in faction_cards)
    assert all(card.timing for card in faction_cards)
    assert all(card.archetype for card in faction_cards)
    assert all(card.effect_text for card in faction_cards)


def test_v03_cards_build_physical_instances() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    deck = build_deck(cards, GameRng(seed=123))

    assert len(deck.draw_pile) == 240
    assert len(deck.research_order_pool) == 60
    assert len(deck.card_instances) == 300
    assert len(set(deck.card_instances)) == 300
    assert all(deck.card_instances[instance_id].card_id.startswith("RA") for instance_id in deck.research_order_pool)


def test_v03_initial_state_uses_research_assignments() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    state = create_initial_state(rules, cards, seed=123)

    assert sum(len(player.hidden_research_orders) for player in state.players.values()) == 8
    assert len(state.deck.research_order_pool) == 52


def test_v03_research_assignment_points_award_multiple_sources() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    player = engine.state.players["P1"]
    player.sources = []
    _put_research_order_on_player(engine, "P1", "RA055")
    engine.event_bus.emit(
        EventType.COMBAT_RESOLVED,
        {
            "applied_deltas": {"red": -6},
            "requested_deltas": {"red": -6},
            "population_before": {"red": 11},
            "population_after": {"red": 5},
        },
    )

    engine.run_phase("research_assignments")

    completed_events = [
        event for event in engine.state.event_log.events if event.event_type == "research_order_completed"
    ]
    assert len(player.sources) == 3
    assert completed_events[-1].payload["card_points"] == 3
    assert completed_events[-1].payload["source_reward"] == 3


def test_v03_research_assignment_checks_current_state_condition() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _put_research_order_on_player(engine, "P1", "RA001")

    engine.run_phase("research_assignments")

    completed_events = [
        event for event in engine.state.event_log.events if event.event_type == EventType.RESEARCH_ORDER_COMPLETED
    ]
    assert completed_events[-1].payload["card_id"] == "RA001"
    assert completed_events[-1].payload["source_reward"] == 1


def test_v03_card_text_power_bonus_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R01", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    payload = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.WORLD_HISTORY_POWER_CALCULATED
    ][-1]
    assert payload["base_power"]["red"] == 4
    assert payload["total_power"]["red"] == 6
    assert any(modifier["card_id"] == "R01" and modifier["amount"] == 2 for modifier in payload["power_modifiers"])


def test_v03_conditional_propaganda_card_text_power_bonus_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R03", engine.state.urn)
    _move_card_to_zone(engine, "R37", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["R37", None, None, None]

    engine.run_phase("world_history_and_combat")

    payload = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.WORLD_HISTORY_POWER_CALCULATED
    ][-1]
    assert payload["base_power"]["red"] == 3
    assert payload["propaganda_power"]["red"] == 1
    assert payload["total_power"]["red"] == 8
    assert any(modifier["card_id"] == "R03" and modifier["amount"] == 2 for modifier in payload["power_modifiers"])
    assert any(modifier["card_id"] == "R37" and modifier["amount"] == 2 for modifier in payload["power_modifiers"])


def test_v03_card_text_recruitment_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "G02", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert engine.state.factions["yellow"].population == 7
    assert combat["population_effects"][0]["card_id"] == "G02"
    assert combat["population_effects"][0]["applied_delta"] == 1


def test_v03_card_text_neutralize_most_population_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    engine.state.factions["red"].population = 12
    engine.state.factions["black"].population = 5
    engine.state.neutral_population = 68
    _move_card_to_zone(engine, "S09", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert engine.state.factions["red"].population == 10
    assert combat["population_effects"][0]["target_faction_id"] == "red"
    assert combat["population_effects"][0]["applied_delta"] == -2


def test_v03_card_text_destroy_neutral_population_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "GR19", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert engine.state.neutral_population == 78
    assert combat["population_effects"][0]["card_id"] == "GR19"
    assert combat["population_effects"][0]["destroyed_population_delta"] == 1


def test_v03_card_text_target_lowest_population_overrides_transition() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    engine.state.factions["black"].population = 4
    engine.state.factions["yellow"].population = 8
    engine.state.factions["green"].population = 9
    engine.state.neutral_population = 74
    _move_card_to_zone(engine, "R29", engine.state.urn)
    _move_card_to_zone(engine, "G02", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert {"attacker": "red", "defender": "black"} in combat["attack_pairs"]
    assert any(effect["card_id"] == "R29" and effect["defender"] == "black" for effect in combat["target_effects"])


def test_v03_card_text_combat_impact_bonus_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R09", engine.state.urn)
    _move_card_to_zone(engine, "GR59", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    red_attack = next(record for record in combat["attack_records"] if record["attacker"] == "red")
    assert red_attack["defender"] == "green"
    assert red_attack["base_impact"] == 1
    assert red_attack["impact"] == 2
    assert any(modifier["card_id"] == "R09" and modifier["reason"] == "add_combat_impact" for modifier in red_attack["modifiers"])


def test_v03_card_text_first_attack_can_be_prevented() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R09", engine.state.urn)
    _move_card_to_zone(engine, "GR58", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    red_attack = next(record for record in combat["attack_records"] if record["attacker"] == "red")
    assert red_attack["defender"] == "green"
    assert red_attack["prevented"] is True
    assert red_attack["impact"] == 0
    assert any(modifier["card_id"] == "GR58" and modifier["reason"] == "prevent_first_attack" for modifier in red_attack["modifiers"])


def test_v03_card_text_combat_impact_reduction_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "S01", engine.state.urn)
    _move_card_to_zone(engine, "R52", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    black_attack = next(record for record in combat["attack_records"] if record["attacker"] == "black")
    assert black_attack["defender"] == "red"
    assert black_attack["base_impact"] == 1
    assert black_attack["impact"] == 0
    assert any(modifier["card_id"] == "R52" and modifier["reason"] == "reduce_first_impact" for modifier in black_attack["modifiers"])


def test_v03_cards_validate_cli() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate-cards",
            "--rules",
            str(RULES_V03),
            "--cards",
            str(CARDS_V03),
        ],
    )

    assert result.exit_code == 0
    assert "Cards OK" in result.stdout
    assert "cards=300" in result.stdout


def test_v03_game_runs_with_imported_cards() -> None:
    rules = load_rules_config(RULES_V03)
    rules.game_id = "v03-card-smoke"
    rules.round_flow.max_rounds = 1
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)

    result = GameEngine(rules, cards, bots=bots, seed=321).run_game()

    assert result.rounds_played == 1
    assert result.event_count > 0
    assert {"red", "black", "yellow", "green"} <= set(result.final_populations)
