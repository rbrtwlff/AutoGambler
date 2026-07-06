from __future__ import annotations

from pathlib import Path

from wsim.bots.base import BotDecision
from wsim.config import load_cards_config, load_rules_config
from wsim.core.state import PlannedAction, RevealedAction
from wsim.engine import GameEngine


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


class FixedActionBot:
    def __init__(self, card_id: str, action_type: str, acting_faction: str, target_faction: str) -> None:
        self.card_id = card_id
        self.action_type = action_type
        self.acting_faction = acting_faction
        self.target_faction = target_faction

    def choose_cards_to_commit(self, context, options, rng):
        choice = [self.card_id] if [self.card_id] in options else []
        return BotDecision(choice=choice, reason="fixed commit")

    def choose_action_type(self, context, options, rng):
        return BotDecision(choice=self.action_type, reason="fixed action")

    def choose_acting_faction(self, context, options, rng):
        return BotDecision(choice=self.acting_faction, reason="fixed acting faction")

    def choose_target_faction(self, context, options, rng):
        return BotDecision(choice=self.target_faction, reason="fixed target faction")


def _engine(seed: int = 123) -> GameEngine:
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    return GameEngine(rules, cards, seed=seed)


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


def test_planning_commits_cards_from_hand() -> None:
    engine = _engine()
    _set_hand(engine, "P1", ["red_action_03"])
    engine.bots["P1"] = FixedActionBot("red_action_03", "support", "red", "red")
    for player_id in ["P2", "P3", "P4"]:
        _set_hand(engine, player_id, [])

    engine.run_phase("planning")

    assert "red_action_03" not in engine.state.players["P1"].hand
    assert engine.state.planned_actions["P1"].committed_card_ids == ["red_action_03"]
    assert any(event["event_type"] == "action_committed" for event in engine.state.export_events_as_dicts())


def test_initiative_uses_fewest_committed_cards_first() -> None:
    engine = _engine()
    engine.state.planned_actions = {
        "P1": PlannedAction(player_id="P1", committed_card_ids=["a", "b"], action_type="support", acting_faction_id="red", target_faction_id="red"),
        "P2": PlannedAction(player_id="P2", committed_card_ids=[], action_type="support", acting_faction_id="black", target_faction_id="black"),
        "P3": PlannedAction(player_id="P3", committed_card_ids=["c"], action_type="attack", acting_faction_id="yellow", target_faction_id="red"),
    }

    engine.run_phase("reveal")

    assert [action.player_id for action in engine.state.revealed_actions] == ["P2", "P3", "P1"]


def test_initiative_tiebreaker_clockwise_from_start_player() -> None:
    engine = _engine()
    engine.state.round.start_player_id = "P3"
    engine.state.planned_actions = {
        player_id: PlannedAction(
            player_id=player_id,
            committed_card_ids=["x"],
            action_type="support",
            acting_faction_id="red",
            target_faction_id="red",
        )
        for player_id in ["P1", "P2", "P3", "P4"]
    }

    engine.run_phase("reveal")

    assert [action.player_id for action in engine.state.revealed_actions] == ["P3", "P4", "P1", "P2"]


def test_support_increases_population_and_reduces_neutral_pool() -> None:
    engine = _engine()
    _remove_card_from_locations(engine, "red_action_03")
    engine.state.revealed_actions = [
        RevealedAction(
            player_id="P1",
            committed_card_ids=["red_action_03"],
            action_type="support",
            acting_faction_id="red",
            target_faction_id="red",
            strength=2,
            initiative_count=1,
            reveal_order=0,
        )
    ]

    engine.run_phase("action_resolution")

    assert engine.state.factions["red"].population == 7
    assert engine.state.neutral_population == 78


def test_support_uses_only_available_neutral_population() -> None:
    engine = _engine()
    engine.state.neutral_population = 1
    engine.state.revealed_actions = [
        RevealedAction(
            player_id="P1",
            committed_card_ids=[],
            action_type="support",
            acting_faction_id="red",
            target_faction_id="red",
            strength=5,
            initiative_count=0,
            reveal_order=0,
        )
    ]

    engine.run_phase("action_resolution")

    assert engine.state.factions["red"].population == 6
    assert engine.state.neutral_population == 0


def test_attack_reduces_population_without_going_negative() -> None:
    engine = _engine()
    engine.state.factions["red"].population = 1
    engine.state.revealed_actions = [
        RevealedAction(
            player_id="P1",
            committed_card_ids=[],
            action_type="attack",
            acting_faction_id="black",
            target_faction_id="red",
            strength=5,
            initiative_count=0,
            reveal_order=0,
        )
    ]

    engine.run_phase("action_resolution")

    assert engine.state.factions["red"].population == 0
    assert engine.state.neutral_population == 80


def test_action_resolution_events_are_logged() -> None:
    engine = _engine()
    _remove_card_from_locations(engine, "red_action_03")
    engine.state.revealed_actions = [
        RevealedAction(
            player_id="P1",
            committed_card_ids=["red_action_03"],
            action_type="support",
            acting_faction_id="red",
            target_faction_id="red",
            strength=2,
            initiative_count=1,
            reveal_order=0,
        )
    ]

    engine.run_phase("action_resolution")
    event_types = [event["event_type"] for event in engine.state.export_events_as_dicts()]

    assert "population_changed" in event_types
    assert "action_resolved" in event_types
    assert "card_discarded" in event_types
