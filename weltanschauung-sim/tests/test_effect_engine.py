from pathlib import Path

import pytest

from wsim.config import ConfigError, load_cards_config, load_rules_config
from wsim.core.events import EventBus
from wsim.core.models import CardConfig
from wsim.core.state import (
    DeckState,
    FactionState,
    GameRng,
    GameState,
    PlayerState,
    PropagandaTrackState,
    RevealedAction,
    RoundState,
)
from wsim.engine.effects import EffectEngine


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "configs" / "rules" / "base_rules.yaml"


def make_state() -> GameState:
    return GameState(
        seed=1,
        rng=GameRng(1),
        players={
            "P1": PlayerState(
                id="P1",
                seat=0,
                public_faction_id="red",
                secret_faction_id="red",
                roles=["journalist"],
                hand=[],
                max_sources=3,
            )
        },
        factions={
            "red": FactionState(id="red", population=5),
            "black": FactionState(id="black", population=5),
            "yellow": FactionState(id="yellow", population=5),
            "green": FactionState(id="green", population=5),
        },
        neutral_population=80,
        deck=DeckState(draw_pile=["draw_1"]),
        propaganda_track=PropagandaTrackState(slots=[None, None, None], overflow="remove_oldest"),
        round=RoundState(start_player_id="P1", journalist_player_id="P1", media_mogul_player_id="P1", round_number=1),
    )


def make_engine(cards: list[CardConfig], state: GameState) -> EffectEngine:
    rules = load_rules_config(RULES_PATH)
    event_bus = EventBus(run_id="test", game_id="test_game", event_log=state.event_log, round_number=1)
    return EffectEngine(rules, cards, event_bus)


def test_on_reveal_add_strength_works():
    card = CardConfig(
        id="attack_bonus",
        name="Attack Bonus",
        type="action",
        faction="red",
        action_effects=[
            {
                "trigger": "on_reveal",
                "type": "add_strength",
                "amount": 1,
                "conditions": [{"type": "action_type_is", "value": "attack"}],
            }
        ],
    )
    state = make_state()
    action = RevealedAction(
        player_id="P1",
        committed_card_ids=[card.id],
        action_type="attack",
        acting_faction_id="red",
        target_faction_id="black",
        strength=2,
        initiative_count=1,
        reveal_order=0,
    )

    make_engine([card], state).trigger("on_reveal", state=state, revealed_action=action)

    assert action.strength == 3
    assert state.event_log.events[-1].event_type == "effect_triggered"


def test_propaganda_effect_in_slot_2_triggers_only_in_slot_2():
    card = CardConfig(
        id="slot_card",
        name="Slot Card",
        type="propaganda",
        propaganda_effects=[
            {
                "trigger": "when_in_propaganda_before_action_resolution",
                "type": "add_strength",
                "amount": 2,
                "conditions": [{"type": "this_card_slot_is", "position": 2}],
            }
        ],
    )
    filler = CardConfig(id="filler", name="Filler", type="propaganda")
    state = make_state()
    state.propaganda_track.slots = ["filler", "slot_card", None]
    action = RevealedAction(
        player_id="P1",
        committed_card_ids=[],
        action_type="attack",
        acting_faction_id="red",
        target_faction_id="black",
        strength=1,
        initiative_count=0,
        reveal_order=0,
    )
    engine = make_engine([card, filler], state)

    engine.trigger("when_in_propaganda_before_action_resolution", state=state, revealed_action=action)
    assert action.strength == 3

    state.propaganda_track.slots = ["slot_card", "filler", None]
    action.strength = 1
    engine.trigger("when_in_propaganda_before_action_resolution", state=state, revealed_action=action)
    assert action.strength == 1


def test_propaganda_contains_faction_count_works():
    card = CardConfig(
        id="red_prop_1",
        name="Red Prop 1",
        type="propaganda",
        faction="red",
        propaganda_effects=[
            {
                "trigger": "when_in_propaganda_before_action_resolution",
                "type": "add_strength",
                "amount": 1,
                "conditions": [{"type": "propaganda_contains_faction_count", "faction": "this_card", "min_count": 2}],
            }
        ],
    )
    sibling = CardConfig(id="red_prop_2", name="Red Prop 2", type="propaganda", faction="red")
    state = make_state()
    state.propaganda_track.slots = ["red_prop_1", "red_prop_2", None]
    action = RevealedAction(
        player_id="P1",
        committed_card_ids=[],
        action_type="support",
        acting_faction_id="red",
        target_faction_id="red",
        strength=1,
        initiative_count=0,
        reveal_order=0,
    )

    make_engine([card, sibling], state).trigger(
        "when_in_propaganda_before_action_resolution",
        state=state,
        revealed_action=action,
    )

    assert action.strength == 2


def test_unmet_conditions_do_nothing():
    card = CardConfig(
        id="support_only",
        name="Support Only",
        type="action",
        action_effects=[
            {
                "trigger": "on_reveal",
                "type": "add_strength",
                "amount": 5,
                "conditions": [{"type": "action_type_is", "value": "support"}],
            }
        ],
    )
    state = make_state()
    action = RevealedAction(
        player_id="P1",
        committed_card_ids=[card.id],
        action_type="attack",
        acting_faction_id="red",
        target_faction_id="black",
        strength=1,
        initiative_count=1,
        reveal_order=0,
    )

    make_engine([card], state).trigger("on_reveal", state=state, revealed_action=action)

    assert action.strength == 1
    assert not state.event_log.events


def test_unknown_effect_types_raise_config_error(tmp_path):
    cards_yaml = tmp_path / "bad_cards.yaml"
    cards_yaml.write_text(
        """
cards:
  - id: broken
    name: Broken
    type: action
    action_effects:
      - trigger: on_reveal
        type: impossible_effect
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown effect type"):
        load_cards_config(cards_yaml, rules_config=load_rules_config(RULES_PATH))
