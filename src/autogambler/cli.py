from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from autogambler.analytics import summarize_winners
from autogambler.config import load_config
from autogambler.engine import SimulationEngine
from autogambler.io import write_simulation_outputs

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("check-config")
def check_config(config_dir: Path = typer.Option(Path("configs"), help="Directory containing YAML configs.")) -> None:
    bundle = load_config(config_dir)
    console.print(
        f"Config OK: {bundle.game.game_id} with {len(bundle.game.players)} players, "
        f"{len(bundle.game.factions)} factions, {len(bundle.cards.cards)} cards."
    )


@app.command()
def simulate(
    games: int = typer.Option(1, min=1, help="Number of games to simulate."),
    seed: int = typer.Option(1, help="Base seed. Game n uses seed+n."),
    config_dir: Path = typer.Option(Path("configs"), help="Directory containing YAML configs."),
    outputs_dir: Path | None = typer.Option(None, help="Directory for generated outputs."),
) -> None:
    bundle = load_config(config_dir)
    engine = SimulationEngine(bundle)
    results = [engine.run_game(game_index=index, seed=seed + index) for index in range(games)]

    target_outputs_dir = outputs_dir or Path(bundle.analysis.outputs_dir)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = write_simulation_outputs(results, target_outputs_dir, run_id)

    table = Table(title="Simulation Summary")
    table.add_column("Faction")
    table.add_column("Wins", justify="right")
    for row in summarize_winners(results).iter_rows(named=True):
        table.add_row(str(row["winner_faction_id"]), str(row["wins"]))
    console.print(table)
    console.print(f"Outputs written to: {target_outputs_dir.resolve()}")
    for label, path in paths.items():
        console.print(f"- {label}: {path}")


if __name__ == "__main__":
    app()
