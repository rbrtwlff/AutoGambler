from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from wsim.dashboard.services import (
    DEFAULT_DASHBOARD_GAMES,
    DEFAULT_DASHBOARD_SEED,
    OUTPUTS_DIR,
    chart_files,
    default_config_paths,
    ensure_run_report,
    export_chatgpt_package,
    export_single_game,
    game_ids,
    list_run_dirs,
    list_yaml_files,
    random_seed,
    read_report,
    read_table,
    render_single_game,
    run_dashboard_simulation,
)


def main() -> None:
    st.set_page_config(page_title="weltanschauung-sim", layout="wide")
    st.title("weltanschauung-sim")

    selected_run = _run_selector()
    tabs = st.tabs(
        [
            "Start / Simulation starten",
            "Overview",
            "Population",
            "Propaganda",
            "World History",
            "Combat",
            "Victory",
            "Sources / Journalist",
            "Research Assignments",
            "Single Game Viewer",
            "Raw Data",
            "Export fuer ChatGPT",
        ]
    )

    with tabs[0]:
        _start_tab()
    with tabs[1]:
        _overview_tab(selected_run)
    with tabs[2]:
        _chart_tab(selected_run, ["average_population_by_round.html", "neutral_population_by_round.html", "destroyed_population_by_round.html"])
    with tabs[3]:
        _chart_tab(selected_run, ["propaganda_power_by_round.html", "activated_propaganda_power_by_round.html"])
        _json_metric_tab(selected_run, "metrics_propaganda.json", "Propagandametriken")
    with tabs[4]:
        _chart_tab(selected_run, ["final_power_by_round.html"])
        _json_metric_tab(selected_run, "metrics_v0_3.json", "Weltgeschichte")
    with tabs[5]:
        _chart_tab(selected_run, ["combat_impact_histogram.html"])
        _json_metric_tab(selected_run, "metrics_v0_3.json", "Kampf")
    with tabs[6]:
        _chart_tab(selected_run, ["winrates_by_faction.html", "winrates_by_player_position.html", "winrates_by_victory_type.html", "round_length_histogram.html"])
    with tabs[7]:
        _chart_tab(selected_run, ["sources_by_round.html", "journalist_changes.html", "media_mogul_distribution.html"])
    with tabs[8]:
        _json_metric_tab(selected_run, "metrics_v0_3.json", "Rechercheauftraege")
    with tabs[9]:
        _single_game_tab(selected_run)
    with tabs[10]:
        _raw_data_tab(selected_run)
    with tabs[11]:
        _export_tab(selected_run)


def _run_selector() -> Path | None:
    runs = list_run_dirs()
    with st.sidebar:
        st.header("Run laden")
        if not runs:
            st.info("Noch keine Simulationsergebnisse in outputs/runs vorhanden.")
            return None
        labels = [run.name for run in runs]
        selected = st.selectbox("Vorhandener Run", labels)
        return runs[labels.index(selected)]


def _start_tab() -> None:
    st.subheader("Simulation starten")
    defaults = default_config_paths()
    rules_path = _path_select("Regeldatei", defaults["rules"].parent, defaults["rules"])
    cards_path = _path_select("Kartendatei", defaults["cards"].parent, defaults["cards"])
    bots_path = _path_select("Botdatei", defaults["bots"].parent, defaults["bots"])

    col_games, col_seed = st.columns(2)
    with col_games:
        games = st.number_input("Anzahl Spiele", min_value=1, max_value=1_000_000, value=DEFAULT_DASHBOARD_GAMES, step=10)
        st.caption("Fuer den ersten Test ist eine kleine Spielzahl vorausgewaehlt.")
    with col_seed:
        seed = st.number_input("Seed", min_value=1, max_value=2_147_483_647, value=DEFAULT_DASHBOARD_SEED, step=1)
        if st.button("Zufaelligen Seed einsetzen"):
            st.session_state["dashboard_seed"] = random_seed()
            st.rerun()
        if "dashboard_seed" in st.session_state:
            seed = st.session_state["dashboard_seed"]
            st.caption(f"Aktueller Zufalls-Seed: {seed}")

    if st.button("Simulation starten", type="primary"):
        try:
            with st.spinner("Simulation laeuft. Das kann je nach Spielzahl dauern."):
                result = run_dashboard_simulation(
                    rules_path=rules_path,
                    cards_path=cards_path,
                    bots_path=bots_path,
                    games=int(games),
                    seed=int(seed),
                    output_dir=None,
                    show_progress=False,
                )
            st.success(f"Simulation fertig: {result.output_dir}")
            st.info("Der neue Run erscheint nach dem Neuladen in der Seitenleiste.")
        except Exception as exc:  # pragma: no cover - Streamlit displays the user-facing message.
            st.error(f"Simulation konnte nicht gestartet werden: {exc}")


def _overview_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    try:
        metrics = ensure_run_report(run_dir)
        overview = metrics.get("overview", {})
        st.subheader("Overview")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Spiele", overview.get("game_count", 0))
        col_b.metric("Durchschnittliche Runden", _fmt(overview.get("rounds", {}).get("average")))
        col_c.metric("Saboteur-Siegquote", _fmt_pct(overview.get("saboteur_win_rate")))
        st.markdown(read_report(run_dir))
    except Exception as exc:
        st.error(f"Run konnte nicht geladen werden: {exc}")


def _last_games_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    st.subheader("Last 1000 Games")
    st.dataframe(read_table(run_dir, "game_summaries", limit=1000), use_container_width=True)


def _chart_tab(run_dir: Path | None, names: list[str]) -> None:
    if not _require_run(run_dir):
        return
    available = {path.name: path for path in chart_files(run_dir)}
    for name in names:
        path = available.get(name)
        if path is None:
            st.info(f"Grafik nicht vorhanden: {name}")
            continue
        components.html(path.read_text(encoding="utf-8"), height=520, scrolling=True)


def _json_metric_tab(run_dir: Path | None, filename: str, title: str) -> None:
    if not _require_run(run_dir):
        return
    st.subheader(title)
    path = run_dir / filename
    try:
        if not path.exists():
            ensure_run_report(run_dir)
        st.json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        st.error(f"{title} konnten nicht geladen werden: {exc}")


def _interesting_games_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    st.subheader("Interesting Games")
    games = read_table(run_dir, "game_summaries", limit=1000)
    if games.is_empty():
        st.info("Keine Spieldaten vorhanden.")
        return
    interesting = games.filter(
        (games["winner_type"] == "saboteur")
        | (games["tie_info"].is_not_null())
        | (games["rounds_played"] <= games["rounds_played"].median())
    )
    st.dataframe(interesting, use_container_width=True)


def _single_game_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    options = game_ids(run_dir)
    if not options:
        st.info("Keine Spiele in diesem Run gefunden.")
        return
    selected = st.selectbox("Spiel auswaehlen", options)
    analysis = st.checkbox("Analysemodus mit geheimen Informationen", value=True)
    if st.button("Spiel anzeigen"):
        try:
            st.markdown(render_single_game(run_dir, selected, analysis=analysis))
        except ValueError as exc:
            st.error(str(exc))
    if st.button("Einzelspiel als Markdown exportieren"):
        try:
            st.success(f"Export erstellt: {export_single_game(run_dir, selected, 'markdown')}")
        except ValueError as exc:
            st.error(str(exc))


def _raw_data_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    table_name = st.selectbox("Datensatz", ["game_summaries", "round_summaries", "bot_metrics"])
    st.dataframe(read_table(run_dir, table_name), use_container_width=True)


def _export_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    st.subheader("Export fuer ChatGPT")
    st.write("Erzeugt einen Ordner `analysis_package/` und die Datei `analysis_package.zip` fuer ChatGPT.")
    if st.button("Analysepaket fuer ChatGPT exportieren", type="primary"):
        try:
            st.success(f"Analysepaket erstellt: {export_chatgpt_package(run_dir)}")
        except Exception as exc:
            st.error(f"Export fehlgeschlagen: {exc}")


def _path_select(label: str, directory: Path, default_path: Path) -> Path:
    files = list_yaml_files(directory)
    labels = [file.name for file in files]
    default_index = labels.index(default_path.name) if default_path.name in labels else 0
    selected = st.selectbox(label, labels, index=default_index)
    return files[labels.index(selected)]


def _require_run(run_dir: Path | None) -> bool:
    if run_dir is None:
        st.info("Bitte zuerst eine Simulation starten oder einen vorhandenen Run in der Seitenleiste laden.")
        return False
    return True


def _fmt(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_pct(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


if __name__ == "__main__":
    main()
