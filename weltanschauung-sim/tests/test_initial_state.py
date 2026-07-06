from __future__ import annotations

from pathlib import Path

import yaml

from wsim.config import load_cards_config, load_rules_config
from wsim.engine import create_initial_state


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _base_state(seed: int = 123):
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    return create_initial_state(rules, cards, seed=seed)


def _saboteur_rules(tmp_path: Path):
    with BASE_RULES.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    data["roles"]["saboteur"] = {"enabled": True, "count": 1}
    path = tmp_path / "saboteur_rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_rules_config(path)


def test_initial_population_matches_config() -> None:
    state = _base_state()

    assert state.factions["red"].population == 5
    assert state.factions["black"].population == 5
    assert state.factions["yellow"].population == 5
    assert state.factions["green"].population == 5
    assert state.neutral_population == 80


def test_total_population_matches_config() -> None:
    state = _base_state()

    assert state.total_population() == 100


def test_all_players_are_created() -> None:
    state = _base_state()

    assert list(state.players) == ["P1", "P2", "P3", "P4"]


def test_secret_factions_are_distributed_one_each_without_saboteur() -> None:
    state = _base_state()
    secret_factions = [player.secret_faction_id for player in state.players.values()]

    assert sorted(secret_factions) == ["black", "green", "red", "yellow"]
    assert all(not player.is_saboteur for player in state.players.values())


def test_exactly_one_saboteur_when_enabled(tmp_path: Path) -> None:
    rules = _saboteur_rules(tmp_path)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    state = create_initial_state(rules, cards, seed=456)
    saboteurs = [player for player in state.players.values() if player.is_saboteur]

    assert len(saboteurs) == 1
    assert saboteurs[0].secret_faction_id is None
    assert "saboteur" in saboteurs[0].roles


def test_same_seed_creates_identical_state() -> None:
    first = _base_state(seed=789)
    second = _base_state(seed=789)

    assert first.to_analysis_view() == second.to_analysis_view()


def test_different_seed_creates_different_state() -> None:
    first = _base_state(seed=1)
    second = _base_state(seed=2)

    assert first.to_analysis_view() != second.to_analysis_view()


def test_journalist_is_left_of_start_player() -> None:
    state = _base_state(seed=42)
    player_ids = list(state.players)
    start_index = player_ids.index(state.round.start_player_id)
    expected_journalist = player_ids[(start_index + 1) % len(player_ids)]

    assert state.round.journalist_player_id == expected_journalist
    assert "journalist" in state.players[expected_journalist].roles


def test_starting_hands_have_configured_size() -> None:
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    state = create_initial_state(rules, cards, seed=99)

    for player in state.players.values():
        assert len(player.hand) == rules.draft.starting_hand_size
        assert len(player.hidden_research_orders) == rules.draft.hidden_research_orders
        assert player.max_sources == rules.draft.max_sources


def test_public_view_hides_other_players_secret_information() -> None:
    state = _base_state(seed=321)
    view = state.to_public_view("P1")

    assert "secret_faction_id" in view["players"]["P1"]
    assert "secret_faction_id" not in view["players"]["P2"]
    assert "hand_size" in view["players"]["P2"]


def test_initial_state_contains_setup_events() -> None:
    state = _base_state(seed=123)
    events = state.export_events_as_dicts()

    assert events[0]["event_type"] == "game_started"
    assert events[-1]["event_type"] == "initial_state_created"
    assert any(event["event_type"] == "card_drawn" for event in events)
