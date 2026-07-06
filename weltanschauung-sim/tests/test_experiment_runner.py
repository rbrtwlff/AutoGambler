import json
from pathlib import Path

from typer.testing import CliRunner

from wsim.cli import app
from wsim.engine import ExperimentRunner


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "configs" / "rules" / "base_rules.yaml"
CARDS_PATH = ROOT / "configs" / "cards" / "base_cards.yaml"
BOTS_PATH = ROOT / "configs" / "bots" / "bot_profiles.yaml"


def test_variants_are_loaded_from_directory(tmp_path):
    variants_dir = _variant_dir(tmp_path)

    variants = _runner(tmp_path, variants_dir).load_variants()

    assert [variant.name for variant in variants] == ["aggressive_bots", "propaganda_4_slots"]


def test_rule_and_bot_overrides_work(tmp_path):
    variants_dir = _variant_dir(tmp_path)
    variants = _runner(tmp_path, variants_dir).load_variants()
    by_name = {variant.name: variant for variant in variants}

    assert by_name["propaganda_4_slots"].rules.propaganda.slots == 4
    assert all(bot.aggression == 0.9 for bot in by_name["aggressive_bots"].bots)


def test_multiple_variants_run_and_write_outputs(tmp_path):
    variants_dir = _variant_dir(tmp_path)
    output_dir = tmp_path / "experiment_run"

    result = _runner(tmp_path, variants_dir, output_dir=output_dir, games=2).run()

    assert result.variants == ["aggressive_bots", "propaganda_4_slots"]
    assert (output_dir / "aggressive_bots" / "game_summaries.csv").exists()
    assert (output_dir / "propaganda_4_slots" / "game_summaries.csv").exists()
    assert (output_dir / "comparison_metrics.json").exists()


def test_comparison_report_is_created(tmp_path):
    variants_dir = _variant_dir(tmp_path)
    output_dir = tmp_path / "experiment_report"

    _runner(tmp_path, variants_dir, output_dir=output_dir, games=1).run()

    report = (output_dir / "comparison_report.md").read_text(encoding="utf-8")
    metrics = json.loads((output_dir / "comparison_metrics.json").read_text(encoding="utf-8"))

    assert "Experiment-Vergleich" in report
    assert "Balancing-Warnungen" in report
    assert metrics["variant_count"] == 2


def test_experiment_cli_works(tmp_path):
    variants_dir = _variant_dir(tmp_path)
    output_dir = tmp_path / "cli_experiment"

    result = CliRunner().invoke(
        app,
        [
            "experiment",
            "--base-rules",
            str(RULES_PATH),
            "--variants",
            str(variants_dir),
            "--cards",
            str(CARDS_PATH),
            "--bots",
            str(BOTS_PATH),
            "--games-per-variant",
            "1",
            "--seed",
            "123",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "comparison_report.md").exists()


def _runner(tmp_path: Path, variants_dir: Path, output_dir: Path | None = None, games: int = 1) -> ExperimentRunner:
    return ExperimentRunner(
        base_rules_path=RULES_PATH,
        variants_path=variants_dir,
        cards_path=CARDS_PATH,
        bots_path=BOTS_PATH,
        games_per_variant=games,
        master_seed=123,
        output_dir=output_dir or (tmp_path / "experiment"),
        show_progress=False,
    )


def _variant_dir(tmp_path: Path) -> Path:
    variants_dir = tmp_path / "variants"
    variants_dir.mkdir()
    (variants_dir / "propaganda_4_slots.yaml").write_text(
        "\n".join(
            [
                "name: propaganda_4_slots",
                "rules:",
                "  propaganda:",
                "    slots: 4",
            ]
        ),
        encoding="utf-8",
    )
    (variants_dir / "aggressive_bots.yaml").write_text(
        "\n".join(
            [
                "name: aggressive_bots",
                "bots:",
                "  defaults:",
                "    aggression: 0.9",
                "    risk_tolerance: 0.8",
            ]
        ),
        encoding="utf-8",
    )
    return variants_dir
