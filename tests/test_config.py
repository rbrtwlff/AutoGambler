from pathlib import Path

import pytest
from pydantic import ValidationError

from autogambler.config import ConfigError, GameConfig, load_config


def test_loads_default_config() -> None:
    bundle = load_config(Path("configs"))

    assert bundle.game.population.total == 100
    assert len(bundle.game.factions) == 4
    assert {player.faction_id for player in bundle.game.players} == {"red", "black", "yellow", "green"}


def test_rejects_wrong_start_population_total() -> None:
    raw = {
        "game_id": "bad",
        "population": {"total": 100, "neutral": 81},
        "factions": [{"id": "red", "start_population": 5}],
        "players": [{"id": "p1", "faction_id": "red", "bot_profile_id": "balanced"}],
        "special_roles": {},
        "propaganda_track": {"slots": [{"id": "s1", "label": "S1"}]},
        "limits": {"max_sources_per_player": 3, "starting_hand_size": 0, "draft_pick_count": 0},
        "rounds": {"max_rounds": 1, "phases": ["draft", "planning", "resolution", "upkeep"]},
        "action_types": [{"id": "campaign", "label": "Campaign", "population_delta": 1}],
        "victory_conditions": [{"id": "limit", "type": "highest_population_at_round_limit"}],
    }

    with pytest.raises((ConfigError, ValidationError)):
        GameConfig.model_validate(raw)

