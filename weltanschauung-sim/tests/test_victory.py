from __future__ import annotations

from pathlib import Path

import yaml

from wsim.config import load_cards_config, load_rules_config
from wsim.core.state import RevealedAction
from wsim.engine import GameEngine, VictoryChecker


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _write_rules(tmp_path: Path, data: dict):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_rules_config(path)


def _base_rules_data() -> dict:
    with BASE_RULES.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    assert isinstance(data, dict)
    return data


def _engine(rules=None, seed: int = 123) -> GameEngine:
    rules = rules or load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    return GameEngine(rules, cards, seed=seed)


def test_faction_win_is_detected_at_game_end() -> None:
    engine = _engine()
    engine.state.players["P1"].secret_faction_id = "red"
    engine.state.players["P2"].secret_faction_id = "black"
    engine.state.players["P3"].secret_faction_id = "yellow"
    engine.state.players["P4"].secret_faction_id = "green"
    engine.state.factions["red"].population = 20
    engine.state.factions["black"].population = 5
    engine.state.factions["yellow"].population = 5
    engine.state.factions["green"].population = 5

    result = VictoryChecker(engine.config).check(engine.state, "game_end")

    assert result.is_win
    assert result.winner_type == "faction"
    assert result.winner_faction == "red"
    assert result.winner_player == "P1"
    assert result.winning_condition == "highest_population_at_game_end"


def test_faction_tie_is_handled_as_shared_win() -> None:
    engine = _engine()
    engine.state.players["P1"].secret_faction_id = "red"
    engine.state.players["P2"].secret_faction_id = "black"
    engine.state.players["P3"].secret_faction_id = "yellow"
    engine.state.players["P4"].secret_faction_id = "green"
    engine.state.factions["red"].population = 10
    engine.state.factions["black"].population = 10
    engine.state.factions["yellow"].population = 1
    engine.state.factions["green"].population = 1

    result = VictoryChecker(engine.config).check(engine.state, "game_end")

    assert result.is_win
    assert result.winner_type == "faction"
    assert result.winner_faction is None
    assert result.tie_info is not None
    assert result.tie_info["tied_factions"] == ["red", "black"]
    assert set(result.tie_info["winner_players"]) == {"P1", "P2"}


def test_saboteur_win_is_detected(tmp_path: Path) -> None:
    data = _base_rules_data()
    data["roles"]["saboteur"] = {"enabled": True, "count": 1}
    data["victory"]["saboteur_win_conditions"] = [{"type": "destroyed_population_at_least", "threshold": 1}]
    rules = _write_rules(tmp_path, data)
    engine = _engine(rules)
    saboteur_id = next(player.id for player in engine.state.players.values() if player.is_saboteur)
    engine.state.factions["red"].population = 4

    result = VictoryChecker(rules).check(engine.state, "after_population_change")

    assert result.is_win
    assert result.winner_type == "saboteur"
    assert result.winner_player == saboteur_id
    assert result.winning_condition == "destroyed_population_at_least"


def test_victory_check_respects_timing() -> None:
    engine = _engine()

    result = VictoryChecker(engine.config).check(engine.state, "not_a_configured_time")

    assert not result.is_win
    assert result.details["skipped"] is True


def test_engine_emits_victory_checked_after_population_change(tmp_path: Path) -> None:
    data = _base_rules_data()
    data["roles"]["saboteur"] = {"enabled": True, "count": 1}
    data["victory"]["saboteur_win_conditions"] = [{"type": "destroyed_population_at_least", "threshold": 1}]
    rules = _write_rules(tmp_path, data)
    engine = _engine(rules)
    engine.state.revealed_actions = [
        RevealedAction(
            player_id="P1",
            committed_card_ids=[],
            action_type="attack",
            acting_faction_id="black",
            target_faction_id="red",
            strength=1,
            initiative_count=0,
            reveal_order=0,
        )
    ]

    engine.run_phase("action_resolution")
    events = engine.state.export_events_as_dicts()

    assert any(event["event_type"] == "victory_checked" for event in events)
    assert any(event["event_type"] == "game_ended" for event in events)
    assert engine.ended_by == "victory"
    assert engine.winner_type == "saboteur"


def test_game_result_contains_winner_data() -> None:
    engine = _engine()
    engine.state.players["P1"].secret_faction_id = "red"
    engine.state.players["P2"].secret_faction_id = "black"
    engine.state.players["P3"].secret_faction_id = "yellow"
    engine.state.players["P4"].secret_faction_id = "green"
    engine.state.factions["red"].population = 20
    engine.state.factions["black"].population = 5
    engine.state.factions["yellow"].population = 5
    engine.state.factions["green"].population = 5

    engine._check_victory("game_end")
    result = engine._build_result()

    assert result.ended_by == "max_rounds"
    assert result.winner_type == "faction"
    assert result.winner_player == "P1"
    assert result.winner_faction == "red"
    assert result.winning_condition == "highest_population_at_game_end"
