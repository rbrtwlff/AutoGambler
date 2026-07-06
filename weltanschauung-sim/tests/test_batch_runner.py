from pathlib import Path

import polars as pl

from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import SimulationBatchRunner


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "configs" / "rules" / "base_rules.yaml"
CARDS_PATH = ROOT / "configs" / "cards" / "base_cards.yaml"
BOTS_PATH = ROOT / "configs" / "bots" / "bot_profiles.yaml"


def load_batch_inputs():
    rules = load_rules_config(RULES_PATH)
    cards = load_cards_config(CARDS_PATH, rules_config=rules)
    bots = load_bots_config(BOTS_PATH, rules_config=rules)
    return rules, cards, bots


def run_batch(output_dir: Path, games: int = 10, seed: int = 123):
    rules, cards, bots = load_batch_inputs()
    return SimulationBatchRunner(
        rules=rules,
        cards=cards,
        bots=bots,
        games=games,
        master_seed=seed,
        output_dir=output_dir,
        show_progress=False,
    ).run()


def test_batch_runner_runs_100_games_without_error(tmp_path):
    result = run_batch(tmp_path / "run_100", games=100, seed=123)

    assert result.game_summary_count == 100
    assert result.round_summary_count > 0


def test_same_seed_produces_same_summaries(tmp_path):
    run_batch(tmp_path / "run_a", games=12, seed=987)
    run_batch(tmp_path / "run_b", games=12, seed=987)

    games_a = (tmp_path / "run_a" / "game_summaries.csv").read_text(encoding="utf-8")
    games_b = (tmp_path / "run_b" / "game_summaries.csv").read_text(encoding="utf-8")
    rounds_a = (tmp_path / "run_a" / "round_summaries.csv").read_text(encoding="utf-8")
    rounds_b = (tmp_path / "run_b" / "round_summaries.csv").read_text(encoding="utf-8")

    assert games_a.replace("run_a", "RUN") == games_b.replace("run_b", "RUN")
    assert rounds_a.replace("run_a", "RUN") == rounds_b.replace("run_b", "RUN")


def test_output_files_are_created(tmp_path):
    output_dir = tmp_path / "run_files"
    run_batch(output_dir, games=3, seed=456)

    assert (output_dir / "run_metadata.json").exists()
    assert (output_dir / "game_summaries.parquet").exists()
    assert (output_dir / "game_summaries.csv").exists()
    assert (output_dir / "round_summaries.parquet").exists()
    assert (output_dir / "round_summaries.csv").exists()
    assert (output_dir / "event_logs_sample.jsonl").exists()


def test_round_summary_contains_population_columns(tmp_path):
    output_dir = tmp_path / "run_rounds"
    run_batch(output_dir, games=5, seed=789)

    frame = pl.read_csv(output_dir / "round_summaries.csv")

    assert frame.height > 0
    assert "red_population" in frame.columns
    assert "black_population" in frame.columns
    assert "yellow_population" in frame.columns
    assert "green_population" in frame.columns
    assert "neutral_population" in frame.columns
