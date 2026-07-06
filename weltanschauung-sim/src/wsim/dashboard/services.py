from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel

from wsim.analytics import GameInspectError, export_analysis_package, export_game, generate_report, inspect_game, render_game_markdown
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import SimulationBatchRunner


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUTS_DIR = PROJECT_ROOT / "outputs" / "runs"


class DashboardRunResult(BaseModel):
    run_id: str
    output_dir: Path
    games: int
    seed: int
    report_path: Path
    metrics_path: Path


def default_config_paths() -> dict[str, Path]:
    return {
        "rules": PROJECT_ROOT / "configs" / "rules" / "base_rules.yaml",
        "cards": PROJECT_ROOT / "configs" / "cards" / "base_cards.yaml",
        "bots": PROJECT_ROOT / "configs" / "bots" / "bot_profiles.yaml",
    }


def list_yaml_files(directory: str | Path) -> list[Path]:
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []
    return sorted(file for file in path.glob("*.yaml") if file.is_file())


def list_run_dirs(outputs_dir: str | Path = OUTPUTS_DIR) -> list[Path]:
    path = Path(outputs_dir)
    if not path.exists():
        return []
    return sorted(
        (directory for directory in path.iterdir() if directory.is_dir()),
        key=lambda directory: directory.stat().st_mtime,
        reverse=True,
    )


def random_seed() -> int:
    return random.SystemRandom().randint(1, 2_147_483_647)


def create_run_dir(seed: int, outputs_dir: str | Path = OUTPUTS_DIR) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(outputs_dir) / f"dashboard_{timestamp}_seed_{seed}"


def run_dashboard_simulation(
    *,
    rules_path: str | Path,
    cards_path: str | Path,
    bots_path: str | Path,
    games: int,
    seed: int,
    output_dir: str | Path | None = None,
    show_progress: bool = False,
) -> DashboardRunResult:
    if games < 1:
        raise ValueError("Die Anzahl Spiele muss mindestens 1 sein.")

    rules_path = Path(rules_path)
    cards_path = Path(cards_path)
    bots_path = Path(bots_path)
    output_path = Path(output_dir) if output_dir is not None else create_run_dir(seed)

    rules = load_rules_config(rules_path)
    cards = load_cards_config(cards_path, rules_config=rules)
    bots = load_bots_config(bots_path, rules_config=rules)
    batch_result = SimulationBatchRunner(
        rules=rules,
        cards=cards,
        bots=bots,
        games=games,
        master_seed=seed,
        output_dir=output_path,
        show_progress=show_progress,
        source_paths={"rules": str(rules_path), "cards": str(cards_path), "bots": str(bots_path)},
    ).run()
    generate_report(output_path)
    return DashboardRunResult(
        run_id=batch_result.run_id,
        output_dir=output_path,
        games=games,
        seed=seed,
        report_path=output_path / "report.md",
        metrics_path=output_path / "metrics.json",
    )


def ensure_run_report(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir)
    metrics_path = run_path / "metrics.json"
    report_path = run_path / "report.md"
    if metrics_path.exists() and report_path.exists():
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    return generate_report(run_path)


def read_report(run_dir: str | Path) -> str:
    ensure_run_report(run_dir)
    report_path = Path(run_dir) / "report.md"
    return report_path.read_text(encoding="utf-8") if report_path.exists() else "Kein Report vorhanden."


def read_table(run_dir: str | Path, table_name: str, *, limit: int | None = None) -> pl.DataFrame:
    run_path = Path(run_dir)
    csv_path = run_path / f"{table_name}.csv"
    parquet_path = run_path / f"{table_name}.parquet"
    if csv_path.exists():
        frame = pl.read_csv(csv_path)
    elif parquet_path.exists():
        frame = pl.read_parquet(parquet_path)
    else:
        return pl.DataFrame()
    return frame.tail(limit) if limit is not None and frame.height > limit else frame


def chart_files(run_dir: str | Path) -> list[Path]:
    charts_dir = Path(run_dir) / "charts"
    if not charts_dir.exists():
        ensure_run_report(run_dir)
    if not charts_dir.exists():
        return []
    return sorted(charts_dir.glob("*.html"))


def game_ids(run_dir: str | Path, *, limit: int = 1000) -> list[str]:
    frame = read_table(run_dir, "game_summaries", limit=limit)
    if frame.is_empty():
        return []
    rows = frame.select(["game_index", "game_id"]).to_dicts()
    return [f"{row['game_index']} | {row['game_id']}" for row in rows]


def render_single_game(run_dir: str | Path, selected_game: str, *, analysis: bool = True) -> str:
    game_id = selected_game.split("|", 1)[0].strip()
    try:
        report = inspect_game(run_dir, game_id, analysis_mode=analysis)
    except GameInspectError as exc:
        raise ValueError(str(exc)) from exc
    return render_game_markdown(report)


def export_single_game(run_dir: str | Path, selected_game: str, export_format: str = "markdown") -> Path:
    game_id = selected_game.split("|", 1)[0].strip()
    try:
        return export_game(run_dir, game_id, export_format=export_format, analysis_mode=True)
    except GameInspectError as exc:
        raise ValueError(str(exc)) from exc


def export_chatgpt_package(run_dir: str | Path) -> Path:
    return export_analysis_package(run_dir).zip_path
