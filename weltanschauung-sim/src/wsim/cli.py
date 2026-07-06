from __future__ import annotations

import typer
from rich.console import Console

from wsim import __version__

app = typer.Typer(no_args_is_help=True, help="weltanschauung-sim Kommandozeile.")
console = Console()


@app.callback()
def main() -> None:
    """weltanschauung-sim Kommandozeile."""


@app.command()
def version() -> None:
    """Zeigt die installierte Version."""
    console.print(f"weltanschauung-sim {__version__}")


if __name__ == "__main__":
    app()
