from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from wsim import __version__
from wsim.analytics import GameInspectError, export_game, generate_charts, generate_report, inspect_game, render_game_markdown
from wsim.config import ConfigError, load_bots_config, load_cards_config, load_rules_config
from wsim.engine import GameEngine, SimulationBatchRunner

app = typer.Typer(no_args_is_help=True, help="weltanschauung-sim Kommandozeile.")
console = Console()


@app.callback()
def main() -> None:
    """weltanschauung-sim Kommandozeile."""


@app.command()
def version() -> None:
    """Zeigt die installierte Version."""
    console.print(f"weltanschauung-sim {__version__}")


@app.command("validate-config")
def validate_config(
    rules: str = typer.Option(..., "--rules", help="Pfad zur Regeln-YAML-Datei."),
) -> None:
    """Validiert eine Regeln-Config."""
    try:
        config = load_rules_config(rules)
    except ConfigError as exc:
        console.print(f"[red]Config invalid:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        "[green]Config OK:[/green] "
        f"{config.game_id} | players={config.player_count} | "
        f"factions={len(config.factions)} | propaganda_slots={config.propaganda.slots}"
    )


@app.command("validate-cards")
def validate_cards(
    rules: str = typer.Option(..., "--rules", help="Pfad zur Regeln-YAML-Datei."),
    cards: str = typer.Option(..., "--cards", help="Pfad zur Karten-YAML-Datei."),
) -> None:
    """Validiert eine Karten-Config gegen eine Regeln-Config."""
    try:
        rules_config = load_rules_config(rules)
        card_configs = load_cards_config(cards, rules_config=rules_config)
    except ConfigError as exc:
        console.print(f"[red]Cards invalid:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    enabled_count = sum(1 for card in card_configs if card.enabled)
    disabled_count = len(card_configs) - enabled_count
    console.print(
        "[green]Cards OK:[/green] "
        f"cards={len(card_configs)} | enabled={enabled_count} | disabled={disabled_count}"
    )


@app.command("run-one")
def run_one(
    rules: str = typer.Option(..., "--rules", help="Pfad zur Regeln-YAML-Datei."),
    cards: str = typer.Option(..., "--cards", help="Pfad zur Karten-YAML-Datei."),
    seed: int = typer.Option(123, "--seed", help="Seed fuer reproduzierbare Simulation."),
) -> None:
    """Fuehrt ein einzelnes Spiel mit Stub-Phasen aus."""
    try:
        rules_config = load_rules_config(rules)
        card_configs = load_cards_config(cards, rules_config=rules_config)
        result = GameEngine(rules_config, card_configs, seed=seed).run_game()
    except ConfigError as exc:
        console.print(f"[red]Run failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[green]Run complete[/green]")
    console.print(f"game_id: {result.game_id}")
    console.print(f"seed: {result.seed}")
    console.print(f"rounds_played: {result.rounds_played}")
    console.print(f"ended_by: {result.ended_by}")
    console.print(f"winner_type: {result.winner_type}")
    console.print(f"winner_player: {result.winner_player}")
    console.print(f"winner_faction: {result.winner_faction}")
    console.print(f"winning_condition: {result.winning_condition}")
    console.print(f"events: {result.event_count}")
    console.print(f"final_populations: {result.final_populations}")


@app.command("run")
def run_batch(
    rules: str = typer.Option(..., "--rules", help="Pfad zur Regeln-YAML-Datei."),
    cards: str = typer.Option(..., "--cards", help="Pfad zur Karten-YAML-Datei."),
    bots: str = typer.Option(..., "--bots", help="Pfad zur Bots-YAML-Datei."),
    games: int = typer.Option(..., "--games", min=1, help="Anzahl der zu simulierenden Spiele."),
    seed: int = typer.Option(123, "--seed", help="Master-Seed fuer reproduzierbare Batch-Laeufe."),
    output: Path = typer.Option(..., "--output", help="Ausgabeordner fuer diesen Lauf."),
) -> None:
    """Fuehrt viele Spiele aus und speichert Analyse-Dateien."""
    try:
        rules_config = load_rules_config(rules)
        card_configs = load_cards_config(cards, rules_config=rules_config)
        bot_configs = load_bots_config(bots, rules_config=rules_config)
        result = SimulationBatchRunner(
            rules=rules_config,
            cards=card_configs,
            bots=bot_configs,
            games=games,
            master_seed=seed,
            output_dir=output,
            source_paths={"rules": rules, "cards": cards, "bots": bots},
        ).run()
    except ConfigError as exc:
        console.print(f"[red]Batch run failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[green]Batch run complete[/green]")
    console.print(f"run_id: {result.run_id}")
    console.print(f"games: {result.games}")
    console.print(f"master_seed: {result.master_seed}")
    console.print(f"game_summaries: {result.game_summary_count}")
    console.print(f"round_summaries: {result.round_summary_count}")
    console.print(f"event_sample_rows: {result.event_sample_count}")
    console.print(f"output: {result.output_dir}")


@app.command("report")
def report(
    run: Path = typer.Option(..., "--run", help="Ausgabeordner eines Simulationslaufs."),
) -> None:
    """Erzeugt metrics.json und report.md fuer einen Simulationslauf."""
    try:
        metrics = generate_report(run)
    except ValueError as exc:
        console.print(f"[red]Report failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[green]Report complete[/green]")
    console.print(f"run_id: {metrics['run_id']}")
    console.print(f"games: {metrics['overview']['game_count']}")
    console.print(f"metrics: {run / 'metrics.json'}")
    console.print(f"report: {run / 'report.md'}")


@app.command("charts")
def charts(
    run: Path = typer.Option(..., "--run", help="Ausgabeordner eines Simulationslaufs."),
) -> None:
    """Erzeugt lokale Plotly-HTML-Grafiken fuer einen Simulationslauf."""
    try:
        chart_files = generate_charts(run)
    except ValueError as exc:
        console.print(f"[red]Charts failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[green]Charts complete[/green]")
    console.print(f"charts: {run / 'charts'}")
    console.print(f"files: {len(chart_files)}")


@app.command("inspect")
def inspect(
    run: Path = typer.Option(..., "--run", help="Ausgabeordner eines Simulationslaufs."),
    game_id: str = typer.Option(..., "--game-id", help="Spielindex oder gespeicherte Game-ID."),
    analysis: bool = typer.Option(False, "--analysis", help="Zeigt Analyseinformationen wie geheime Fraktionen."),
) -> None:
    """Zeigt ein einzelnes Spiel Runde fuer Runde in der Konsole."""
    try:
        report_data = inspect_game(run, game_id, analysis_mode=analysis)
    except GameInspectError as exc:
        console.print(f"[red]Inspect failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(render_game_markdown(report_data))


@app.command("export-game")
def export_game_command(
    run: Path = typer.Option(..., "--run", help="Ausgabeordner eines Simulationslaufs."),
    game_id: str = typer.Option(..., "--game-id", help="Spielindex oder gespeicherte Game-ID."),
    export_format: str = typer.Option(..., "--format", help="Exportformat: markdown oder json."),
    analysis: bool = typer.Option(True, "--analysis/--public", help="Exportiert Analyseinformationen, wenn vorhanden."),
) -> None:
    """Exportiert ein einzelnes Spiel als Markdown oder JSON."""
    if export_format not in {"markdown", "json"}:
        console.print("[red]Export failed:[/red] --format must be markdown or json")
        raise typer.Exit(code=1)
    try:
        output_path = export_game(run, game_id, export_format=export_format, analysis_mode=analysis)
    except GameInspectError as exc:
        console.print(f"[red]Export failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[green]Game export complete[/green]")
    console.print(f"output: {output_path}")


if __name__ == "__main__":
    app()
