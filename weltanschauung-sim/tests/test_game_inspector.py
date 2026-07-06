import json
from pathlib import Path

from typer.testing import CliRunner

from wsim.analytics import GameInspectError, export_game, inspect_game
from wsim.cli import app
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import SimulationBatchRunner


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "configs" / "rules" / "base_rules.yaml"
CARDS_PATH = ROOT / "configs" / "cards" / "base_cards.yaml"
BOTS_PATH = ROOT / "configs" / "bots" / "bot_profiles.yaml"


def create_run(output_dir: Path, games: int = 3):
    rules = load_rules_config(RULES_PATH)
    cards = load_cards_config(CARDS_PATH, rules_config=rules)
    bots = load_bots_config(BOTS_PATH, rules_config=rules)
    SimulationBatchRunner(
        rules=rules,
        cards=cards,
        bots=bots,
        games=games,
        master_seed=222,
        output_dir=output_dir,
        show_progress=False,
        source_paths={"rules": str(RULES_PATH), "cards": str(CARDS_PATH), "bots": str(BOTS_PATH)},
    ).run()


def test_existing_game_can_be_exported(tmp_path):
    run_dir = tmp_path / "inspect_run"
    create_run(run_dir)

    output_path = export_game(run_dir, 1, export_format="markdown")

    assert output_path == run_dir / "games" / "game_000001.md"
    assert output_path.exists()


def test_unknown_game_id_has_clear_error(tmp_path):
    run_dir = tmp_path / "inspect_run"
    create_run(run_dir)

    try:
        inspect_game(run_dir, 999)
    except GameInspectError as exc:
        assert "Game not found: 999" in str(exc)
    else:
        raise AssertionError("Expected GameInspectError")


def test_markdown_contains_round_flow(tmp_path):
    run_dir = tmp_path / "inspect_run"
    create_run(run_dir)

    output_path = export_game(run_dir, 0, export_format="markdown")
    markdown = output_path.read_text(encoding="utf-8")

    assert "Runde-fuer-Runde-Zusammenfassung" in markdown
    assert "### Runde 1" in markdown
    assert "Propagandaleiste" in markdown
    assert "Aktionen" in markdown


def test_json_export_is_valid(tmp_path):
    run_dir = tmp_path / "inspect_run"
    create_run(run_dir)

    output_path = export_game(run_dir, 2, export_format="json")
    data = json.loads(output_path.read_text(encoding="utf-8"))

    assert data["game_index"] == 2
    assert data["rounds"]
    assert data["victory"]["winner_type"] in {"faction", "saboteur", "draw", "none"}


def test_export_game_cli_works(tmp_path):
    run_dir = tmp_path / "inspect_run"
    create_run(run_dir)

    result = CliRunner().invoke(app, ["export-game", "--run", str(run_dir), "--game-id", "0", "--format", "json"])

    assert result.exit_code == 0
    assert (run_dir / "games" / "game_000000.json").exists()


def test_inspect_cli_unknown_game_fails_cleanly(tmp_path):
    run_dir = tmp_path / "inspect_run"
    create_run(run_dir)

    result = CliRunner().invoke(app, ["inspect", "--run", str(run_dir), "--game-id", "99"])

    assert result.exit_code == 1
    assert "Game not found: 99" in result.output
