import json
from pathlib import Path

from typer.testing import CliRunner

from wsim.analytics import generate_charts, generate_report
from wsim.analytics.report import build_balancing_warnings
from wsim.cli import app
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.core.models import AnalyticsConfig
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


def test_advanced_metrics_are_created_from_eventlog(tmp_path):
    run_dir = tmp_path / "advanced_report_run"
    create_run(run_dir, games=4)

    metrics = generate_report(run_dir)

    assert (run_dir / "metrics_cards.json").exists()
    assert (run_dir / "metrics_propaganda.json").exists()
    assert (run_dir / "metrics_bots.json").exists()
    assert metrics["cards"]["event_log_available"] is True
    assert metrics["cards"]["card_count"] > 0
    assert metrics["propaganda_advanced"]["event_log_available"] is True
    assert metrics["bots"]["bot_metrics_available"] is True


def test_report_mentions_advanced_metrics(tmp_path):
    run_dir = tmp_path / "advanced_report_run"
    create_run(run_dir, games=3)

    generate_report(run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "Kartenanalyse" in report
    assert "Propagandaanalyse" in report
    assert "Botanalyse" in report


def test_missing_eventlogs_are_handled_cleanly(tmp_path):
    run_dir = tmp_path / "missing_events_run"
    create_run(run_dir, games=2)
    (run_dir / "event_logs_sample.jsonl").unlink()

    metrics = generate_report(run_dir)

    assert metrics["cards"]["event_log_available"] is False
    assert metrics["propaganda_advanced"]["event_log_available"] is False
    assert (run_dir / "metrics_cards.json").exists()
    assert (run_dir / "metrics_propaganda.json").exists()
    assert (run_dir / "metrics_bots.json").exists()


def test_balancing_warnings_appear_for_artificial_outliers():
    metrics = _warning_metrics(
        faction_rates={"red": 0.42, "black": 0.08, "yellow": 0.25, "green": 0.25},
        saboteur_rate=0.31,
        player_rates={"P1": 0.38},
        average_rounds=4,
        rounds=[3, 4, 5, 9],
        card_overrides={
            "red_attack": {"play_count": 3, "propaganda_count": 0, "ineffectiveness_rate": 0.75, "swing_value": 25}
        },
        dominant_slot_share=0.72,
        bot_fallback_rate=0.28,
        research_draw_count=4,
        research_completion_rate=0.0,
    )

    warnings = build_balancing_warnings(metrics, AnalyticsConfig())
    messages = "\n".join(warning["message"] for warning in warnings)

    assert "Red win rate" in messages
    assert "Black wins only" in messages
    assert "Saboteur win rate" in messages
    assert "Player P1 wins" in messages
    assert "Average game length" in messages
    assert "Card red_attack" in messages
    assert "Propaganda slot" in messages
    assert "Research Orders completion rate" in messages
    assert "fallback decision rate" in messages


def test_no_balancing_warnings_for_plausible_metrics():
    metrics = _warning_metrics(
        faction_rates={"red": 0.25, "black": 0.24, "yellow": 0.26, "green": 0.25},
        saboteur_rate=0.05,
        player_rates={"P1": 0.24, "P2": 0.25, "P3": 0.26, "P4": 0.25},
        average_rounds=8,
        rounds=[7, 8, 8, 9],
        card_overrides={
            "steady_card": {"play_count": 4, "propaganda_count": 0, "ineffectiveness_rate": 0.25, "swing_value": 6}
        },
        dominant_slot_share=0.34,
        bot_fallback_rate=0.02,
        research_draw_count=4,
        research_completion_rate=0.25,
    )

    assert build_balancing_warnings(metrics, AnalyticsConfig()) == []


def test_balancing_warning_thresholds_come_from_config():
    metrics = _warning_metrics(
        faction_rates={"red": 0.25, "black": 0.25, "yellow": 0.25, "green": 0.25},
        saboteur_rate=0.0,
        player_rates={"P1": 0.25},
        average_rounds=8,
        rounds=[8, 8, 8, 8],
    )

    loose = AnalyticsConfig(faction_winrate_max=0.30, faction_winrate_min=0.10)
    strict = AnalyticsConfig(faction_winrate_max=0.20, faction_winrate_min=0.10)

    assert build_balancing_warnings(metrics, loose) == []
    assert any(warning["code"] == "faction_winrate_high" for warning in build_balancing_warnings(metrics, strict))


def test_report_contains_warning_section(tmp_path):
    run_dir = tmp_path / "warning_report_run"
    create_run(run_dir, games=2)

    generate_report(run_dir)
    report = (run_dir / "report.md").read_text(encoding="utf-8")

    assert "## WARNINGS" in report


def _warning_metrics(
    *,
    faction_rates: dict[str, float],
    saboteur_rate: float,
    player_rates: dict[str, float],
    average_rounds: float,
    rounds: list[int],
    card_overrides: dict[str, dict[str, float]] | None = None,
    dominant_slot_share: float = 0.0,
    bot_fallback_rate: float = 0.0,
    research_draw_count: int = 0,
    research_completion_rate: float = 1.0,
) -> dict:
    return {
        "overview": {
            "faction_win_rates": faction_rates,
            "saboteur_win_rate": saboteur_rate,
            "player_position_win_rates": player_rates,
            "rounds": {
                "average": average_rounds,
                "median": average_rounds,
                "min": min(rounds),
                "max": max(rounds),
                "values": rounds,
                "early_decision_rate": 0.0,
            },
        },
        "cards": {
            "cards": card_overrides or {},
            "research_order_draw_count": research_draw_count,
            "research_order_completion_rate": research_completion_rate,
        },
        "propaganda_advanced": {
            "dominant_slot": 2,
            "dominant_slot_share": dominant_slot_share,
        },
        "bots": {
            "by_bot_type": {
                "heuristic": {
                    "fallback_random_decision_rate": bot_fallback_rate,
                }
            }
        },
    }
