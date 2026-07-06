from pathlib import Path

from autogambler.actions import legal_actions_for_view
from autogambler.config import load_config
from autogambler.engine import SimulationEngine


def test_legal_actions_are_based_on_config_not_card_ids() -> None:
    bundle = load_config(Path("configs"))
    engine = SimulationEngine(bundle)
    state = engine._initial_state(__import__("random").Random(5), __import__("autogambler.events").events.EventLog())
    player = next(iter(state.players.values()))
    view = engine._view_for_player(state, player.id)

    legal_actions = legal_actions_for_view(bundle, view)

    assert legal_actions
    assert {action.action_type_id for action in legal_actions}.issubset(
        {action_type.id for action_type in bundle.game.action_types}
    )


def test_bot_view_does_not_expose_other_private_hands() -> None:
    bundle = load_config(Path("configs"))
    engine = SimulationEngine(bundle)
    state = engine._initial_state(__import__("random").Random(7), __import__("autogambler.events").events.EventLog())
    player_ids = list(state.players)
    view = engine._view_for_player(state, player_ids[0])

    other_cards = set()
    for other_id in player_ids[1:]:
        other_cards.update(state.players[other_id].hand)

    assert not set(view.own_hand).intersection(other_cards)
    assert not hasattr(view, "players")

