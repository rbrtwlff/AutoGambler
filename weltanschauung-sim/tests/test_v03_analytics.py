from __future__ import annotations

import zipfile
from pathlib import Path

from wsim.analytics import export_analysis_package, export_game, generate_charts, generate_report
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import SimulationBatchRunner


ROOT = Path(__file__).resolve().parents[1]
RULES_V0_3 = ROOT / "configs" / "rules" / "rules_v0_3.yaml"
BASE_CARDS = ROOT / "configs" / "cards" / "base_cards.yaml"
BASE_BOTS = ROOT / "configs" / "bots" / "bot_profiles.yaml"


def create_v03_run(output_dir: Path, games: int = 2) -> None:
    rules = load_rules_config(RULES_V0_3)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    bots = load_bots_config(BASE_BOTS, rules_config=rules)
    SimulationBatchRunner(
        rules=rules,
        cards=cards,
        bots=bots,
        games=games,
        master_seed=909,
        output_dir=output_dir,
        show_progress=False,
        source_paths={"rules": str(RULES_V0_3), "cards": str(BASE_CARDS), "bots": str(BASE_BOTS)},
    ).run()


def test_v03_run_creates_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "v03_report"
    create_v03_run(run_dir)

    metrics = generate_report(run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")

    assert (run_dir / "metrics_v0_3.json").exists()
    assert metrics["v0_3"]["event_log_available"] is True
    assert "v0.3 Analyse" in report
    assert "Weltgeschichte" in report
    assert "Kampf" in report


def test_v03_run_creates_charts(tmp_path: Path) -> None:
    run_dir = tmp_path / "v03_charts"
    create_v03_run(run_dir)

    charts = generate_charts(run_dir)

    expected = {
        "charts/destroyed_population_by_round.html",
        "charts/activated_propaganda_power_by_round.html",
        "charts/final_power_by_round.html",
        "charts/winrates_by_victory_type.html",
        "charts/sources_by_round.html",
        "charts/media_mogul_distribution.html",
        "charts/combat_impact_histogram.html",
    }
    assert expected.intersection(set(charts)) == expected


def test_v03_single_game_export_contains_world_history_and_combat(tmp_path: Path) -> None:
    run_dir = tmp_path / "v03_inspect"
    create_v03_run(run_dir)

    output_path = export_game(run_dir, 0, export_format="markdown")
    markdown = output_path.read_text(encoding="utf-8")

    assert "Urne / Weltgeschichte" in markdown
    assert "Finale Macht" in markdown
    assert "Kampf / angewendete Deltas" in markdown
    assert "Rechercheauftraege" in markdown


def test_v03_analysis_package_contains_v03_metrics(tmp_path: Path) -> None:
    run_dir = tmp_path / "v03_package"
    create_v03_run(run_dir)

    result = export_analysis_package(run_dir)

    assert (result.package_dir / "metrics_v0_3.json").exists()
    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
    assert "analysis_package/metrics_v0_3.json" in names
