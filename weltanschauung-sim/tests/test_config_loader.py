from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wsim.config import ConfigError, load_rules_config
from wsim.cli import app


BASE_RULES = Path("configs/rules/base_rules.yaml")


def _base_data() -> dict:
    with BASE_RULES.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    assert isinstance(data, dict)
    return data


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_valid_config_loads() -> None:
    config = load_rules_config(BASE_RULES)

    assert config.player_count == 4
    assert config.population.total_population == 100
    assert config.population.neutral_start == 80
    assert [faction.id for faction in config.factions] == ["red", "black", "yellow", "green"]
    assert config.draft.starting_hand_size == 5
    assert config.draft.hidden_research_orders == 2
    assert config.draft.max_sources == 3
    assert config.propaganda.slots == 3
    assert config.propaganda.overflow == "remove_oldest"


def test_invalid_population_raises_config_error(tmp_path: Path) -> None:
    data = _base_data()
    data["population"]["neutral_start"] = 79
    path = _write_config(tmp_path, data)

    with pytest.raises(ConfigError, match="Starting faction population"):
        load_rules_config(path)


def test_duplicate_faction_id_raises_config_error(tmp_path: Path) -> None:
    data = _base_data()
    data["factions"][1] = deepcopy(data["factions"][0])
    path = _write_config(tmp_path, data)

    with pytest.raises(ConfigError, match="Faction ids must be unique"):
        load_rules_config(path)


def test_invalid_propaganda_slots_raises_config_error(tmp_path: Path) -> None:
    data = _base_data()
    data["propaganda"]["slots"] = 0
    path = _write_config(tmp_path, data)

    with pytest.raises(ConfigError, match="propaganda.slots"):
        load_rules_config(path)


def test_cli_validate_config_works() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["validate-config", "--rules", str(BASE_RULES)])

    assert result.exit_code == 0
    assert "Config OK" in result.stdout
    assert "weltanschauung_base" in result.stdout

