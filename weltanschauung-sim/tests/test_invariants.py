from pathlib import Path

import pytest
from typer.testing import CliRunner

from wsim.cli import app
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import GameEngine, InvariantError, validate_game_result_matches_events, validate_game_state


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")
BOTS_PATH = Path("configs/bots/bot_profiles.yaml")


def _inputs():
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    bots = load_bots_config(BOTS_PATH, rules_config=rules)
    return rules, cards, bots


def test_valid_state_passes_invariant_checks():
    rules, cards, _ = _inputs()
    engine = GameEngine(rules, cards, seed=123)

    validate_game_state(rules, engine.state, cards)


def test_validate_game_state_detects_negative_population():
    rules, cards, _ = _inputs()
    engine = GameEngine(rules, cards, seed=123)
    engine.state.factions["red"].population = -1

    with pytest.raises(InvariantError, match="negative"):
        validate_game_state(rules, engine.state, cards)


def test_validate_game_state_detects_duplicate_card_location():
    rules, cards, _ = _inputs()
    engine = GameEngine(rules, cards, seed=123)
    card_id = engine.state.players["P1"].hand[0]
    engine.state.deck.draw_pile.append(card_id)

    with pytest.raises(InvariantError, match="multiple locations"):
        validate_game_state(rules, engine.state, cards)


def test_validate_game_state_detects_event_index_corruption():
    rules, cards, _ = _inputs()
    engine = GameEngine(rules, cards, seed=123)
    engine.state.event_log.events[-1].event_index = 0

    with pytest.raises(InvariantError, match="unique"):
        validate_game_state(rules, engine.state, cards)


def test_game_result_must_match_last_game_ended_event():
    rules, cards, _ = _inputs()
    engine = GameEngine(rules, cards, seed=123)
    result = engine.run_game()
    engine.state.event_log.events[-1].payload["winner_type"] = "saboteur"

    with pytest.raises(InvariantError, match="GameResult does not match"):
        validate_game_result_matches_events(result, engine.state)


def test_smoke_test_cli_runs_100_games(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "smoke-test",
            "--rules",
            str(BASE_RULES),
            "--cards",
            str(BASE_CARDS),
            "--bots",
            str(BOTS_PATH),
            "--games",
            "100",
            "--seed",
            "123",
            "--debug-output",
            str(tmp_path / "debug"),
        ],
    )

    assert result.exit_code == 0
    assert "Smoke test complete" in result.stdout
