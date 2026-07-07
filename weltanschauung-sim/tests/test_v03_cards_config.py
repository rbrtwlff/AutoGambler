from __future__ import annotations

from collections import Counter
from pathlib import Path

from typer.testing import CliRunner

from wsim.cli import app
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.core.events import EventType
from wsim.bots.base import BotDecision
from wsim.core.state import GameRng
from wsim.engine.game_engine import GameEngine, PhaseContext
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
    assert all("TODO" not in note for note in state.round.role_assignment_notes)


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


def test_v03_research_assignments_allow_more_than_three_sources() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    player = engine.state.players["P1"]
    player.sources = ["existing_source_1", "existing_source_2", "existing_source_3"]
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
        event for event in engine.state.event_log.events if event.event_type == EventType.RESEARCH_ORDER_COMPLETED
    ]
    assert player.max_sources == 99
    assert len(player.sources) == 6
    assert completed_events[-1].payload["source_reward"] == 3
    assert completed_events[-1].payload["sources_after"] == 6


def test_v03_research_assignments_summary_has_no_todo_and_refills_to_configured_max() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _put_research_order_on_player(engine, "P1", "RA001")

    engine.run_phase("research_assignments")

    summary_events = [
        event for event in engine.state.event_log.events if event.event_type == EventType.RESEARCH_ASSIGNMENTS_CHECKED
    ]
    assert "todo" not in summary_events[-1].payload
    assert summary_events[-1].payload["max_assignments"] == 2
    assert len(engine.state.players["P1"].hidden_research_orders) == 2


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


def test_v03_journalist_can_remove_propaganda_and_pay_pool_card_cost() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    journalist_id = engine.state.round.journalist_player_id
    media_mogul_id = engine.state.round.media_mogul_player_id

    _move_card_to_zone(engine, "R39", engine.state.deck.removed_from_game)
    engine.state.deck.removed_from_game.remove("R39")
    engine.state.propaganda_track.slots[0] = "R39"
    _move_card_to_zone(engine, "R01", engine.state.journalist_pool)
    _move_card_to_zone(engine, "R02", engine.state.journalist_pool)

    journalist_bot = engine.bots[journalist_id]
    journalist_bot.choose_future_set_or_remove_propaganda = lambda _context, _options, _rng: BotDecision(
        choice="remove_propaganda",
        reason="test removes harmful propaganda",
    )
    journalist_bot.choose_propaganda_to_remove = lambda _context, _options, _rng: BotDecision(
        choice="R39",
        reason="test target propaganda",
    )
    journalist_bot.choose_pool_card_to_discard_as_cost = lambda _context, _options, _rng: BotDecision(
        choice="R01",
        reason="test cost card",
    )
    engine.bots[media_mogul_id].choose_one_of_two_as_new_propaganda = lambda _context, _options, _rng: BotDecision(
        choice="R02",
        reason="test media mogul placement",
    )

    engine.run_phase("journalist_and_media_mogul")

    journalist_events = [
        event for event in engine.state.event_log.events if event.event_type == EventType.JOURNALIST_ACTION_TAKEN
    ]
    assert journalist_events[-1].payload["action"] == "remove_propaganda"
    assert "R39" in engine.state.deck.discard_pile
    assert "R01" in engine.state.deck.discard_pile
    assert engine.state.propaganda_track.get_slots()[0] == "R02"


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


def test_v03_interpretation_authority_uses_activated_propaganda_power() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R01", engine.state.urn)
    _move_card_to_zone(engine, "G02", engine.state.urn)
    _move_card_to_zone(engine, "R37", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "G58", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["R37", "G58", None, None]

    engine.run_phase("world_history_and_combat")

    payload = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.WORLD_HISTORY_POWER_CALCULATED
    ][-1]
    assert payload["propaganda_power"]["yellow"] > payload["propaganda_power"]["red"]
    assert payload["interpretation_authority"] == "yellow"


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


def test_v03_card_text_extra_first_recruitment_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "S25", engine.state.urn)
    _move_card_to_zone(engine, "S20", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert any(effect["reason"] == "extra_first_recruit" and effect["target_faction_id"] == "black" for effect in combat["population_effects"])
    assert any(effect["reason"] == "text_recruit" and effect["target_faction_id"] == "black" and effect["applied_delta"] == 2 for effect in combat["population_effects"])


def test_v03_card_text_recruitment_reduction_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "GR40", engine.state.urn)
    _move_card_to_zone(engine, "S20", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert any(effect["reason"] == "reduce_all_recruit" and effect["target_faction_id"] == "black" for effect in combat["population_effects"])
    assert not any(effect["reason"] == "text_recruit" and effect["target_faction_id"] == "black" for effect in combat["population_effects"])


def test_v03_card_text_recruitment_block_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    engine.state.factions["black"].population = 12
    engine.state.neutral_population = 73
    _move_card_to_zone(engine, "GR23", engine.state.urn)
    _move_card_to_zone(engine, "S20", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert any(effect["reason"] == "recruit_blocked" and effect["target_faction_id"] == "black" for effect in combat["population_effects"])
    assert not any(effect["reason"] == "text_recruit" and effect["target_faction_id"] == "black" for effect in combat["population_effects"])


def test_v03_recruitment_limit_is_applied_after_modifiers() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "S17", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["S17", None, None, None]
    _move_card_to_zone(engine, "S25", engine.state.urn)
    _move_card_to_zone(engine, "S20", engine.state.urn)
    _move_card_to_zone(engine, "S22", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    black_recruit_total = sum(
        int(effect.get("applied_delta", 0))
        for effect in combat["population_effects"]
        if effect.get("reason") == "text_recruit" and effect.get("target_faction_id") == "black"
    )
    assert black_recruit_total == 2
    assert any(effect["reason"] == "recruit_limit" for effect in combat["population_effects"])


def test_v03_card_text_restoration_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    engine.state.neutral_population = 79
    _move_card_to_zone(engine, "S06", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert any(effect["reason"] == "text_restore" and effect["applied_delta"] == 1 for effect in combat["population_effects"])


def test_v03_card_text_first_restoration_can_be_prevented() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    engine.state.neutral_population = 79
    _move_card_to_zone(engine, "S06", engine.state.urn)
    _move_card_to_zone(engine, "GR25", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["GR25", None, None, None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert any(effect["reason"] == "restore_prevented" and effect["applied_delta"] == 0 for effect in combat["population_effects"])
    assert not any(effect["reason"] == "text_restore" and effect["applied_delta"] > 0 for effect in combat["population_effects"])


def test_v03_card_text_removes_slot_one_propaganda() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "GR09", engine.state.urn)
    _move_card_to_zone(engine, "R37", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["R37", None, None, None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert engine.state.propaganda_track.get_slots()[0] is None
    assert "R37" in engine.state.deck.discard_pile
    assert any(effect["removed_card_id"] == "R37" for effect in combat["propaganda_effects"])


def test_v03_card_text_removes_colored_propaganda() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R51", engine.state.urn)
    _move_card_to_zone(engine, "R37", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "S39", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["S39", "R37", None, None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert "R37" not in engine.state.propaganda_track.get_slots()
    assert "S39" in engine.state.propaganda_track.get_slots()
    assert any(effect["removed_card_id"] == "R37" for effect in combat["propaganda_effects"])


def test_v03_card_text_swaps_adjacent_propaganda() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "G19", engine.state.urn)
    _move_card_to_zone(engine, "R37", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "S39", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["R37", "S39", None, None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert engine.state.propaganda_track.get_slots()[:2] == ["S39", "R37"]
    assert any(effect["reason"] == "text_swap_adjacent_propaganda" for effect in combat["propaganda_effects"])


def test_v03_card_text_moves_slot_one_to_slot_four() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "G27", engine.state.urn)
    _move_card_to_zone(engine, "R37", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "S39", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "G20", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["R37", "S39", "G20", None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert engine.state.propaganda_track.get_slots() == ["S39", "G20", None, "R37"]
    assert any(effect["reason"] == "text_move_slot_1_to_slot_4" for effect in combat["propaganda_effects"])


def test_v03_displaced_propaganda_effect_is_applied() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)

    context = PhaseContext(
        config=engine.config,
        cards=engine.cards,
        state=engine.state,
        event_bus=engine.event_bus,
        phase_name="journalist_and_media_mogul",
    )

    effect = engine._apply_v03_displaced_propaganda_effect("R41", context)

    assert effect is not None
    assert effect["reason"] == "v03_displaced_recruit"
    assert effect["target_faction_id"] == "red"
    assert effect["applied_delta"] == 1


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


def test_v03_target_markers_weight_propaganda_over_transition() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R01", engine.state.urn)
    _move_card_to_zone(engine, "S01", engine.state.urn)
    _move_card_to_zone(engine, "R30", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "S39", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["R30", "S39", None, None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert {"attacker": "red", "defender": "yellow"} in combat["attack_pairs"]
    summary = next(effect for effect in combat["target_effects"] if effect["effect_type"] == "v03_target_marker_summary")
    assert summary["markers"]["red"]["black"] == 1
    assert summary["markers"]["red"]["yellow"] == 3


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


def test_v03_card_text_combat_power_tie_can_be_won() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R07", engine.state.urn)
    _move_card_to_zone(engine, "S01", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    red_attack = next(record for record in combat["attack_records"] if record["attacker"] == "red")
    assert red_attack["defender"] == "black"
    assert red_attack["margin"] == 0
    assert red_attack["tie_won"] is True
    assert red_attack["impact"] == 1
    assert combat["combat_tiebreakers"]["red"] == ["R07"]


def test_v03_conditional_power_tie_requires_exact_printed_strength_count() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R23", engine.state.urn)
    _move_card_to_zone(engine, "R52", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    power = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.WORLD_HISTORY_POWER_CALCULATED
    ][-1]
    assert combat["combat_tiebreakers"]["red"] == ["R23"]
    assert any(modifier["card_id"] == "R23" and modifier["amount"] == 2 for modifier in power["power_modifiers"])


def test_v03_card_text_draws_for_fewest_handcards_after_successful_attack() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    before = len(engine.state.players["P1"].hand)
    _move_card_to_zone(engine, "R56", engine.state.urn)
    _move_card_to_zone(engine, "GR59", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert len(engine.state.players["P1"].hand) == before + 1
    assert any(effect["reason"] == "text_draw_fewest_handcards" and effect["player_id"] == "P1" for effect in combat["utility_effects"])


def test_v03_card_text_media_mogul_draws_and_discards() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    media_mogul = engine.state.round.media_mogul_player_id
    _move_card_to_zone(engine, "R01", engine.state.players[media_mogul].hand)
    engine.state.players[media_mogul].hand.remove("R01")
    engine.state.players[media_mogul].hand.insert(0, "R01")
    _move_card_to_zone(engine, "G07", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    draw_effect = next(effect for effect in combat["utility_effects"] if effect["reason"] == "text_media_mogul_draw")
    assert draw_effect["player_id"] == media_mogul
    assert draw_effect["applied_count"] == 1
    assert draw_effect["discard_effect"]["discarded_card_id"] == "R01"
    assert "R01" in engine.state.deck.discard_pile


def test_v03_card_draw_limit_is_applied_after_draw_modifiers() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    media_mogul = engine.state.round.media_mogul_player_id
    _move_card_to_zone(engine, "GR41", engine.state.urn)
    _move_card_to_zone(engine, "G07", engine.state.urn)
    _move_card_to_zone(engine, "G52", engine.state.urn)
    _move_card_to_zone(engine, "G37", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["G37", None, None, None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    media_draws = [
        effect
        for effect in combat["utility_effects"]
        if effect.get("player_id") == media_mogul and "draw" in str(effect.get("reason"))
    ]
    assert sum(effect["applied_count"] for effect in media_draws) == 1
    assert any(effect["requested_count"] > effect["applied_count"] for effect in media_draws)


def test_v03_card_text_journalist_gains_source_after_propaganda_removed() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    journalist = engine.state.round.journalist_player_id
    engine.state.players[journalist].sources.clear()
    _move_card_to_zone(engine, "GR09", engine.state.urn)
    _move_card_to_zone(engine, "GR59", engine.state.urn)
    _move_card_to_zone(engine, "R37", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["R37", None, None, None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert len(engine.state.players[journalist].sources) == 1
    assert any(effect["reason"] == "text_journalist_gain_source" and effect["player_id"] == journalist for effect in combat["utility_effects"])


def test_v03_card_text_journalist_draws_research_order() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    journalist = engine.state.round.journalist_player_id
    before = len(engine.state.players[journalist].hidden_research_orders)
    _move_card_to_zone(engine, "G57", engine.state.urn)
    _move_card_to_zone(engine, "G58", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "G50", engine.state.propaganda_track.slots)
    _move_card_to_zone(engine, "G55", engine.state.propaganda_track.slots)
    engine.state.propaganda_track.slots = ["G58", "G50", "G55", None]

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert len(engine.state.players[journalist].hidden_research_orders) == before + 1
    assert any(effect["reason"] == "text_journalist_draw_research_order" for effect in combat["utility_effects"])


def test_v03_card_text_first_target_change_to_yellow_is_ignored() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    engine.state.factions["yellow"].population = 4
    engine.state.factions["green"].population = 9
    engine.state.neutral_population = 75
    _move_card_to_zone(engine, "G54", engine.state.urn)
    _move_card_to_zone(engine, "R29", engine.state.urn)
    _move_card_to_zone(engine, "S01", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    assert any(effect["effect_type"] == "v03_target_modifier_ignored" and effect["defender"] == "yellow" for effect in combat["target_effects"])
    assert {"attacker": "red", "defender": "yellow"} not in combat["attack_pairs"]


def test_v03_only_one_replacement_effect_applies_to_same_attack() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    _move_card_to_zone(engine, "R16", engine.state.urn)
    _move_card_to_zone(engine, "R58", engine.state.urn)
    _move_card_to_zone(engine, "R01", engine.state.urn)
    _move_card_to_zone(engine, "S01", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    red_attack = next(record for record in combat["attack_records"] if record["attacker"] == "red")
    assert red_attack["destroyed_impact"] == 1
    assert sum(1 for modifier in red_attack["modifiers"] if modifier["reason"] == "destroy_instead_of_neutralize") == 1
    assert any(modifier["reason"] == "replacement_ignored" for modifier in red_attack["modifiers"])


def test_v03_transfer_replacement_moves_population_to_yellow() -> None:
    rules = load_rules_config(RULES_V03)
    cards = load_cards_config(CARDS_V03, rules_config=rules)
    bots = load_bots_config(BOTS, rules_config=rules)
    engine = GameEngine(rules, cards, bots=bots, seed=123)
    engine.state.factions["green"].population = 4
    engine.state.neutral_population = 81
    _move_card_to_zone(engine, "G38", engine.state.urn)
    _move_card_to_zone(engine, "GR59", engine.state.urn)

    engine.run_phase("world_history_and_combat")

    combat = [
        event.payload
        for event in engine.state.event_log.events
        if event.event_type == EventType.COMBAT_RESOLVED
    ][-1]
    yellow_attack = next(record for record in combat["attack_records"] if record["attacker"] == "yellow")
    assert yellow_attack["transferred_impact"] == 1
    assert yellow_attack["transfer_target_faction"] == "yellow"
    assert engine.state.factions["yellow"].population == 6
    assert any(modifier["reason"] == "transfer_instead_of_neutralize" for modifier in yellow_attack["modifiers"])


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
