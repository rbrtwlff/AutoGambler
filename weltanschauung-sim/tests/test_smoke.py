from typer.testing import CliRunner

from wsim import __version__
from wsim.cli import app


def test_version_constant_exists() -> None:
    assert __version__ == "0.1.0"


def test_cli_version_command() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "weltanschauung-sim 0.1.0" in result.stdout


def test_cli_help_command() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "weltanschauung-sim" in result.stdout

