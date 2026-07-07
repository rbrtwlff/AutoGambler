from pathlib import Path

import pytest

from wsim.config import load_cards_config, load_rules_config
from wsim.core.events import EventBus
from wsim.core.models import CardConfig
from wsim.core.state import GameRng, GameRuleError
from wsim.engine import GameEngine, validate_game_state
from wsim.engine.setup import build_deck


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")
BOTS_PATH = Path("configs/bots/bot_profiles.yaml")


def _rules():
    return load_rules_config(BASE_RULES)


def test_count_builds_physical_card_instances():
    cards = [CardConfig(id="test_card", name="Test Card", type="action", count=3)]

    deck = build_deck(cards, GameRng(123))

    assert len(deck.draw_pile) == 3
    assert len(deck.card_instances) == 3
    assert {instance.card_id for instance in deck.card_instances.values()} == {"test_card"}


def test_instance_ids_are_unique_for_copies():
    cards = [CardConfig(id="test_card", name="Test Card", type="action", count=3)]

    deck = build_deck(cards, GameRng(123))

    assert len(set(deck.card_instances)) == 3
    assert sorted(deck.card_instances) == ["test_card__001", "test_card__002", "test_card__003"]


def test_same_card_id_can_have_multiple_instances():
    cards = [CardConfig(id="test_card", name="Test Card", type="action", count=2)]

    deck = build_deck(cards, GameRng(123))

    instances = list(deck.card_instances.values())
    assert instances[0].card_id == instances[1].card_id
    assert instances[0].instance_id != instances[1].instance_id


def test_empty_draw_pile_reshuffles_discard_and_draws():
    rules = _rules()
    deck = build_deck([CardConfig(id="a", name="A", type="action"), CardConfig(id="b", name="B", type="action")], GameRng(1))
    deck.discard_pile = ["a", "b"]
    deck.draw_pile = []
    bus = EventBus(run_id="run", game_id="game")

    drawn = deck.draw_cards(1, config=rules.deck, rng=GameRng(1), event_bus=bus, reason="test")

    assert len(drawn) == 1
    assert {drawn[0], *deck.draw_pile} == {"a", "b"}
    assert any(event["event_type"] == "deck_reshuffled" for event in bus.export_events_as_dicts())


def test_removed_from_game_does_not_return_on_reshuffle():
    rules = _rules()
    deck = build_deck([CardConfig(id="a", name="A", type="action"), CardConfig(id="b", name="B", type="action")], GameRng(1))
    bus = EventBus(run_id="run", game_id="game")
    deck.draw_pile = []
    deck.discard_pile = ["a"]
    deck.removed_from_game = ["b"]

    drawn = deck.draw_cards(2, config=rules.deck, rng=GameRng(1), event_bus=bus, reason="test")

    assert drawn == ["a"]
    assert "b" in deck.removed_from_game
    assert "b" not in deck.draw_pile


def test_hand_and_propaganda_cards_do_not_return_to_draw_pile():
    rules = _rules()
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    engine = GameEngine(rules, cards, seed=123)
    hand_card = engine.state.players["P1"].hand[0]
    propaganda_card = next(instance_id for instance_id, instance in engine.state.deck.card_instances.items() if instance.type == "propaganda")
    _remove_from_locations(engine, propaganda_card)
    engine.state.propaganda_track.slots[0] = propaganda_card
    engine.state.deck.draw_pile = []
    engine.state.deck.discard_pile = []

    drawn = engine.state.deck.draw_cards(1, config=rules.deck, rng=engine.state.rng, event_bus=engine.event_bus, reason="test")

    assert drawn == []
    assert hand_card not in engine.state.deck.draw_pile
    assert propaganda_card not in engine.state.deck.draw_pile


def test_when_not_enough_cards_draw_less():
    rules = _rules()
    rules.deck.when_not_enough_cards = "draw_less"
    deck = build_deck([CardConfig(id="a", name="A", type="action")], GameRng(1))
    bus = EventBus(run_id="run", game_id="game")

    drawn = deck.draw_cards(3, config=rules.deck, rng=GameRng(1), event_bus=bus, reason="test")

    assert drawn == ["a"]
    assert any(event["event_type"] == "not_enough_cards_to_draw" for event in bus.export_events_as_dicts())


def test_when_not_enough_cards_error_raises_game_rule_error():
    rules = _rules()
    rules.deck.when_not_enough_cards = "error"
    deck = build_deck([CardConfig(id="a", name="A", type="action")], GameRng(1))
    bus = EventBus(run_id="run", game_id="game")

    with pytest.raises(GameRuleError):
        deck.draw_cards(3, config=rules.deck, rng=GameRng(1), event_bus=bus, reason="test", game_id="game")


def test_same_seed_produces_same_reshuffle_order():
    rules = _rules()
    first = build_deck([CardConfig(id="a", name="A", type="action"), CardConfig(id="b", name="B", type="action")], GameRng(1))
    second = build_deck([CardConfig(id="a", name="A", type="action"), CardConfig(id="b", name="B", type="action")], GameRng(1))
    first.draw_pile = []
    second.draw_pile = []
    first.discard_pile = ["a", "b"]
    second.discard_pile = ["a", "b"]

    first.draw_cards(2, config=rules.deck, rng=GameRng(99), event_bus=EventBus(run_id="a", game_id="g"), reason="test")
    second.draw_cards(2, config=rules.deck, rng=GameRng(99), event_bus=EventBus(run_id="b", game_id="g"), reason="test")

    assert first.discard_pile == second.discard_pile
    assert first.draw_pile == second.draw_pile


def test_validate_game_state_detects_instance_in_two_locations():
    rules = _rules()
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    engine = GameEngine(rules, cards, seed=123)
    instance_id = engine.state.players["P1"].hand[0]
    engine.state.deck.discard_pile.append(instance_id)

    with pytest.raises(AssertionError, match="multiple locations"):
        validate_game_state(rules, engine.state, cards)


def _remove_from_locations(engine: GameEngine, instance_id: str) -> None:
    for pile in [
        engine.state.deck.draw_pile,
        engine.state.deck.discard_pile,
        engine.state.deck.research_order_pool,
        engine.state.deck.removed_from_game,
    ]:
        while instance_id in pile:
            pile.remove(instance_id)
    for player in engine.state.players.values():
        for zone in [player.hand, player.hidden_research_orders, player.sources]:
            while instance_id in zone:
                zone.remove(instance_id)
