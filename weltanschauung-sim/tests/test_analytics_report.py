import json
from pathlib import Path

from typer.testing import CliRunner

from wsim.analytics import generate_charts, generate_report
from wsim.cli import app
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import SimulationBatchRunner


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "configs" / "rules" / "base_rules.yaml"
CARDS_PATH = ROOT / "configs" / "cards" / "base_cards.yaml"
BOTS_PATH = ROOT / "configs" / "bots" / "bot_profiles.yaml"


def create_run(output_dir: Path, games: int = 8):
    rules = load_rules_config(RULES_PATH)
    cards = load_cards_config(CARDS_PATH, rules_config=rules)
    bots = load_bots_config(BOTS_PATH, rules_config=rules)
    SimulationBatchRunner(
        rules=rules,
        cards=cards,
        bots=bots,
        games=games,
        master_seed=321,
        output_dir=output_dir,
        show_progress=False,
    ).run()


def test_metrics_json_is_created(tmp_path):
    run_dir = tmp_path / "report_run"
    create_run(run_dir)

    generate_report(run_dir)

    assert (run_dir / "metrics.json").exists()


def test_report_md_is_created(tmp_path):
    run_dir = tmp_path / "report_run"
    create_run(run_dir)

    generate_report(run_dir)

    assert (run_dir / "report.md").exists()
    assert "Analysepaket" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_win_rates_sum_plausibly(tmp_path):
    run_dir = tmp_path / "report_run"
    create_run(run_dir)

    metrics = generate_report(run_dir)
    overview = metrics["overview"]
    total_rate = sum(overview["faction_win_rates"].values()) + overview["saboteur_win_rate"]

    assert 0 <= total_rate <= 1
    assert 0 <= overview["known_winner_rate_total"] <= 1


def test_empty_run_is_handled_cleanly(tmp_path):
    run_dir = tmp_path / "empty_run"
    run_dir.mkdir()
    (run_dir / "run_metadata.json").write_text(json.dumps({"games": 0}), encoding="utf-8")

    metrics = generate_report(run_dir)

    assert metrics["overview"]["game_count"] == 0
    assert (run_dir / "metrics.json").exists()
    assert (run_dir / "report.md").exists()


def test_report_cli_works(tmp_path):
    run_dir = tmp_path / "cli_report_run"
    create_run(run_dir, games=3)

    result = CliRunner().invoke(app, ["report", "--run", str(run_dir)])

    assert result.exit_code == 0
    assert (run_dir / "metrics.json").exists()
    assert (run_dir / "report.md").exists()


def test_chart_files_are_created(tmp_path):
    run_dir = tmp_path / "chart_run"
    create_run(run_dir, games=4)

    chart_files = generate_charts(run_dir)

    assert (run_dir / "charts" / "winrates_by_faction.html").exists()
    assert (run_dir / "charts" / "winrates_by_player_position.html").exists()
    assert (run_dir / "charts" / "round_length_histogram.html").exists()
    assert (run_dir / "charts" / "average_population_by_round.html").exists()
    assert (run_dir / "charts" / "final_population_boxplot.html").exists()
    assert (run_dir / "charts" / "neutral_population_by_round.html").exists()
    assert "charts/winrates_by_faction.html" in chart_files


def test_report_links_chart_files(tmp_path):
    run_dir = tmp_path / "linked_chart_run"
    create_run(run_dir, games=4)

    generate_report(run_dir)

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "(charts/winrates_by_faction.html)" in report
    assert "(charts/average_population_by_round.html)" in report


def test_charts_cli_handles_small_runs(tmp_path):
    run_dir = tmp_path / "small_chart_run"
    create_run(run_dir, games=1)

    result = CliRunner().invoke(app, ["charts", "--run", str(run_dir)])

    assert result.exit_code == 0
    assert (run_dir / "charts" / "round_length_histogram.html").exists()
