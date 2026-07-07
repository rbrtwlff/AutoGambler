from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wsim.cli import app
from wsim.config import ConfigError, load_cards_config, load_rules_config


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _cards_data() -> dict:
    with BASE_CARDS.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    assert isinstance(data, dict)
    return data


def _write_cards(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "cards.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_cards_load() -> None:
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)

    assert len(cards) >= 64
    assert {card.type for card in cards} >= {"action", "hybrid", "propaganda", "research_order", "source"}
    assert all(card.count == 1 for card in cards)
    for faction_id in ["red", "black", "yellow", "green"]:
        faction_cards = [card for card in cards if card.faction == faction_id and card.type in {"action", "hybrid"}]
        assert len(faction_cards) >= 8


def test_duplicate_card_ids_raise_config_error(tmp_path: Path) -> None:
    data = _cards_data()
    data["cards"][1] = deepcopy(data["cards"][0])
    path = _write_cards(tmp_path, data)

    with pytest.raises(ConfigError, match="Card ids must be unique"):
        load_cards_config(path, rules_config=load_rules_config(BASE_RULES))


def test_unknown_faction_raises_config_error(tmp_path: Path) -> None:
    data = _cards_data()
    data["cards"][0]["faction"] = "blue"
    path = _write_cards(tmp_path, data)

    with pytest.raises(ConfigError, match="unknown faction blue"):
        load_cards_config(path, rules_config=load_rules_config(BASE_RULES))


def test_disabled_cards_are_detected() -> None:
    cards = load_cards_config(BASE_CARDS, rules_config=load_rules_config(BASE_RULES))
    disabled_cards = [card for card in cards if not card.enabled]

    assert [card.id for card in disabled_cards] == ["source_08"]


def test_cli_validate_cards_works() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate-cards",
            "--rules",
            str(BASE_RULES),
            "--cards",
            str(BASE_CARDS),
        ],
    )

    assert result.exit_code == 0
    assert "Cards OK" in result.stdout
    assert "disabled=1" in result.stdout
