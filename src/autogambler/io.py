from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import polars as pl

from autogambler.engine import SimulationResult


def ensure_outputs_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_simulation_outputs(results: list[SimulationResult], outputs_dir: Path, run_id: str) -> dict[str, Path]:
    ensure_outputs_dir(outputs_dir)
    events_path = outputs_dir / f"{run_id}_events.jsonl"
    summary_path = outputs_dir / f"{run_id}_summary.csv"
    config_note_path = outputs_dir / f"{run_id}_chatgpt_package_todo.md"

    with events_path.open("w", encoding="utf-8") as file:
        for result in results:
            for event in result.events:
                file.write(json.dumps({"game_index": result.game_index, "seed": result.seed, **event}) + "\n")

    summary_rows = [
        {
            "game_index": result.game_index,
            "seed": result.seed,
            "winner_faction_id": result.winner_faction_id,
            "victory_condition_id": result.victory_condition_id,
            "rounds_played": result.rounds_played,
            **{f"population_{key}": value for key, value in result.final_population.items()},
        }
        for result in results
    ]
    pl.DataFrame(summary_rows).write_csv(summary_path)

    config_note_path.write_text(
        "# Analysepaket fuer ChatGPT\n\n"
        "TODO: Hier werden spaeter Config-Snapshot, Summary, Eventauszug und Fragestellung gebuendelt.\n",
        encoding="utf-8",
    )
    return {"events": events_path, "summary": summary_path, "chatgpt_package_todo": config_note_path}


def result_to_dict(result: SimulationResult) -> dict:
    return asdict(result)

