import zipfile
from pathlib import Path

from typer.testing import CliRunner

from wsim.analytics import export_analysis_package
from wsim.cli import app
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import SimulationBatchRunner


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "configs" / "rules" / "base_rules.yaml"
CARDS_PATH = ROOT / "configs" / "cards" / "base_cards.yaml"
BOTS_PATH = ROOT / "configs" / "bots" / "bot_profiles.yaml"


def create_run(output_dir: Path, games: int = 3) -> None:
    rules = load_rules_config(RULES_PATH)
    cards = load_cards_config(CARDS_PATH, rules_config=rules)
    bots = load_bots_config(BOTS_PATH, rules_config=rules)
    SimulationBatchRunner(
        rules=rules,
        cards=cards,
        bots=bots,
        games=games,
        master_seed=515,
        output_dir=output_dir,
        show_progress=False,
        source_paths={"rules": str(RULES_PATH), "cards": str(CARDS_PATH), "bots": str(BOTS_PATH)},
    ).run()


def test_analysis_package_directory_is_created(tmp_path):
    run_dir = tmp_path / "package_run"
    create_run(run_dir)

    result = export_analysis_package(run_dir)

    assert result.package_dir.exists()
    assert (result.package_dir / "report.md").exists()
    assert (result.package_dir / "metrics.json").exists()
    assert (result.package_dir / "metrics_cards.json").exists()
    assert (result.package_dir / "metrics_propaganda.json").exists()
    assert (result.package_dir / "metrics_bots.json").exists()
    assert (result.package_dir / "README_FUER_CHATGPT.md").exists()
    assert (result.package_dir / "interesting_games").exists()


def test_analysis_package_zip_is_created(tmp_path):
    run_dir = tmp_path / "package_zip_run"
    create_run(run_dir)

    result = export_analysis_package(run_dir)

    assert result.zip_path.exists()
    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
    assert "analysis_package/report.md" in names
    assert "analysis_package/README_FUER_CHATGPT.md" in names
    assert any(name.startswith("analysis_package/interesting_games/game_") for name in names)


def test_missing_optional_files_are_skipped_cleanly(tmp_path):
    run_dir = tmp_path / "package_optional_run"
    create_run(run_dir)

    result = export_analysis_package(run_dir)
    readme = (result.package_dir / "README_FUER_CHATGPT.md").read_text(encoding="utf-8")

    assert "comparison_report.md" in result.skipped_optional_files
    assert not (result.package_dir / "comparison_report.md").exists()
    assert "uebersprungen" in readme


def test_analysis_package_cli_works(tmp_path):
    run_dir = tmp_path / "package_cli_run"
    create_run(run_dir)

    result = CliRunner().invoke(app, ["export-analysis-package", "--run", str(run_dir)])

    assert result.exit_code == 0
    assert (run_dir / "analysis_package.zip").exists()
