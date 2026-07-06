from pathlib import Path

from autogambler.config import load_config
from autogambler.engine import SimulationEngine


def test_simulation_is_reproducible_for_same_seed() -> None:
    bundle = load_config(Path("configs"))
    engine = SimulationEngine(bundle)

    first = engine.run_game(game_index=0, seed=123)
    second = engine.run_game(game_index=0, seed=123)

    assert first.winner_faction_id == second.winner_faction_id
    assert first.final_population == second.final_population
    assert [event["type"] for event in first.events] == [event["type"] for event in second.events]


def test_simulation_logs_relevant_events() -> None:
    bundle = load_config(Path("configs"))
    result = SimulationEngine(bundle).run_game(game_index=0, seed=1)
    event_types = {event["type"] for event in result.events}

    assert "game_started" in event_types
    assert "phase_started" in event_types
    assert "action_planned" in event_types
    assert "population_changed" in event_types
    assert "victory_reached" in event_types


def test_population_total_is_conserved() -> None:
    bundle = load_config(Path("configs"))
    result = SimulationEngine(bundle).run_game(game_index=0, seed=2)

    assert sum(result.final_population.values()) <= bundle.game.population.total

