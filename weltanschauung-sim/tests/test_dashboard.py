from pathlib import Path

from typer.testing import CliRunner

from wsim.cli import app
from wsim.dashboard import (
    DEFAULT_DASHBOARD_GAMES,
    DEFAULT_DASHBOARD_SEED,
    default_config_paths,
    export_chatgpt_package,
    list_yaml_files,
    read_table,
    run_dashboard_simulation,
)


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_module_is_importable():
    import wsim.dashboard.app as dashboard_app

    assert callable(dashboard_app.main)


def test_dashboard_lists_default_configs():
    defaults = default_config_paths()

    assert defaults["rules"].exists()
    assert defaults["cards"].exists()
    assert defaults["bots"].exists()
    assert defaults["rules"] in list_yaml_files(defaults["rules"].parent)
    assert DEFAULT_DASHBOARD_GAMES == 10
    assert DEFAULT_DASHBOARD_SEED == 123


def test_dashboard_core_run_and_export_work(tmp_path):
    defaults = default_config_paths()
    output_dir = tmp_path / "dashboard_run"

    result = run_dashboard_simulation(
        rules_path=defaults["rules"],
        cards_path=defaults["cards"],
        bots_path=defaults["bots"],
        games=2,
        seed=123,
        output_dir=output_dir,
        show_progress=False,
    )
    package_path = export_chatgpt_package(output_dir)
    games = read_table(output_dir, "game_summaries")

    assert result.report_path.exists()
    assert result.metrics_path.exists()
    assert package_path.exists()
    assert games.height == 2


def test_dashboard_cli_command_is_registered():
    result = CliRunner().invoke(app, ["dashboard", "--help"])

    assert result.exit_code == 0
    assert "Streamlit" in result.output
