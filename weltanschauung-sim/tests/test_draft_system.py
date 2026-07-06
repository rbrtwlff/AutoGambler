from __future__ import annotations

from pathlib import Path

import yaml

from wsim.config import load_cards_config, load_rules_config
from wsim.engine import GameEngine


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _rules_for_draft(tmp_path: Path, *, draw_count: int = 2, pick_count: int = 1):
    with BASE_RULES.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    data["round_flow"]["max_rounds"] = 1
    data["draft"]["draw_count"] = draw_count
    data["draft"]["pick_count"] = pick_count
    data["draft"]["pass_count"] = max(0, draw_count - pick_count)
    path = tmp_path / "draft_rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_rules_config(path)


def _engine(tmp_path: Path, *, seed: int = 123, draw_count: int = 2, pick_count: int = 1) -> GameEngine:
    rules = _rules_for_draft(tmp_path, draw_count=draw_count, pick_count=pick_count)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    return GameEngine(rules, cards, seed=seed)


def test_draft_draws_cards_from_deck(tmp_path: Path) -> None:
    engine = _engine(tmp_path, draw_count=2, pick_count=1)
    before_deck_size = len(engine.state.deck.draw_pile)

    engine.run_phase("draft")

    assert len(engine.state.deck.draw_pile) == before_deck_size - 8


def test_draft_changes_hands(tmp_path: Path) -> None:
    engine = _engine(tmp_path, draw_count=2, pick_count=1)
    before_hand_sizes = {player_id: len(player.hand) for player_id, player in engine.state.players.items()}

    engine.run_phase("draft")

    after_hand_sizes = {player_id: len(player.hand) for player_id, player in engine.state.players.items()}
    assert after_hand_sizes == {player_id: size + 1 for player_id, size in before_hand_sizes.items()}


def test_draft_deck_size_changes_correctly(tmp_path: Path) -> None:
    engine = _engine(tmp_path, draw_count=3, pick_count=1)
    before_deck_size = len(engine.state.deck.draw_pile)
    before_discard_size = len(engine.state.deck.discard_pile)

    engine.run_phase("draft")

    player_count = len(engine.state.players)
    assert len(engine.state.deck.draw_pile) == before_deck_size - (3 * player_count)
    assert len(engine.state.deck.discard_pile) == before_discard_size + (2 * player_count)


def test_draft_discard_pile_receives_unpicked_cards(tmp_path: Path) -> None:
    engine = _engine(tmp_path, draw_count=2, pick_count=1)

    engine.run_phase("draft")

    assert len(engine.state.deck.discard_pile) == 4


def test_draft_events_are_created(tmp_path: Path) -> None:
    engine = _engine(tmp_path, draw_count=2, pick_count=1)

    engine.run_phase("draft")
    event_types = [event["event_type"] for event in engine.state.export_events_as_dicts()]

    assert "draft_started" in event_types
    assert "card_drawn" in event_types
    assert "card_drafted" in event_types
    assert "card_discarded" in event_types
    assert "draft_finished" in event_types


def test_draft_is_reproducible_for_same_seed(tmp_path: Path) -> None:
    first = _engine(tmp_path, seed=999, draw_count=2, pick_count=1)
    second = _engine(tmp_path, seed=999, draw_count=2, pick_count=1)

    first.run_phase("draft")
    second.run_phase("draft")

    assert first.state.to_analysis_view() == second.state.to_analysis_view()

