from __future__ import annotations

import typer
from rich.console import Console

from wsim import __version__
from wsim.config import ConfigError, load_cards_config, load_rules_config
from wsim.engine import GameEngine

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


if __name__ == "__main__":
    app()
