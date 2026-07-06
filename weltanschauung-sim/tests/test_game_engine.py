from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from wsim.cli import app
from wsim.config import load_cards_config, load_rules_config
from wsim.engine import GameEngine, GameResult


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _load_engine(seed: int = 123) -> GameEngine:
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    return GameEngine(rules, cards, seed=seed)


def _rules_with_max_rounds(tmp_path: Path, max_rounds: int):
    with BASE_RULES.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    data["round_flow"]["max_rounds"] = max_rounds
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_rules_config(path)


def test_phases_run_in_configured_order() -> None:
    engine = _load_engine()
    engine.run_round()
    events = engine.state.export_events_as_dicts()
    phase_events = [event for event in events if event["event_type"] == "phase_started"]

    assert [event["payload"]["phase"] for event in phase_events] == engine.config.round_flow.phases


def test_round_counts_up() -> None:
    engine = _load_engine()

    engine.run_round()
    engine.run_round()

    assert engine.state.round.round_number == 2


def test_game_ends_at_max_rounds(tmp_path: Path) -> None:
    rules = _rules_with_max_rounds(tmp_path, max_rounds=2)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    result = GameEngine(rules, cards, seed=123).run_game()

    assert result.rounds_played == 2
    assert result.ended_by == "max_rounds"


def test_phase_started_events_are_created() -> None:
    engine = _load_engine()
    result = engine.run_game()
    phase_started_events = [
        event for event in engine.state.export_events_as_dicts() if event["event_type"] == "phase_started"
    ]

    assert phase_started_events
    assert len(phase_started_events) == result.rounds_played * len(engine.config.round_flow.phases)


def test_run_game_returns_game_result() -> None:
    result = _load_engine(seed=777).run_game()

    assert isinstance(result, GameResult)
    assert result.game_id == "weltanschauung_base"
    assert result.seed == 777
    assert result.event_count > 0
    assert set(result.final_populations) == {"red", "black", "yellow", "green"}
    assert sum(result.final_populations.values()) <= 100


def test_cli_run_one_works() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-one",
            "--rules",
            str(BASE_RULES),
            "--cards",
            str(BASE_CARDS),
            "--seed",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert "Run complete" in result.stdout
    assert "ended_by: max_rounds" in result.stdout
