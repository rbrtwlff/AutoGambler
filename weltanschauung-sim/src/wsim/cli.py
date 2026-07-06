from __future__ import annotations

import typer
from rich.console import Console

from wsim import __version__
from wsim.config import ConfigError, load_rules_config

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


if __name__ == "__main__":
    app()
