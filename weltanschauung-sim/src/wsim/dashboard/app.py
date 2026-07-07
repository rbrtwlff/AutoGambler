from __future__ import annotations

import json
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
    _apply_dashboard_style()

    selected_run = _run_selector()
    _dashboard_header(selected_run)
    tabs = st.tabs(
        [
            "Start",
            "Ueberblick",
            "Letzte Spiele",
            "Population",
            "Propaganda",
            "Weltgeschichte",
            "Kampf",
            "Sieg",
            "Quellen & Rollen",
            "Recherche",
            "Auffaellige Spiele",
            "Einzelspiel",
            "Rohdaten",
            "ChatGPT Export",
        ]
    )

    with tabs[0]:
        _start_tab()
    with tabs[1]:
        _overview_tab(selected_run)
    with tabs[2]:
        _last_games_tab(selected_run)
    with tabs[3]:
        _chart_tab(selected_run, ["average_population_by_round.html", "neutral_population_by_round.html", "destroyed_population_by_round.html"])
    with tabs[4]:
        _chart_tab(selected_run, ["propaganda_power_by_round.html", "activated_propaganda_power_by_round.html"])
        _json_metric_tab(selected_run, "metrics_propaganda.json", "Propagandametriken")
    with tabs[5]:
        _chart_tab(selected_run, ["final_power_by_round.html"])
        _json_metric_tab(selected_run, "metrics_v0_3.json", "Weltgeschichte")
    with tabs[6]:
        _chart_tab(selected_run, ["combat_impact_histogram.html"])
        _json_metric_tab(selected_run, "metrics_v0_3.json", "Kampf")
    with tabs[7]:
        _chart_tab(selected_run, ["winrates_by_faction.html", "winrates_by_player_position.html", "winrates_by_victory_type.html", "round_length_histogram.html"])
    with tabs[8]:
        _chart_tab(selected_run, ["sources_by_round.html", "journalist_changes.html", "media_mogul_distribution.html"])
    with tabs[9]:
        _json_metric_tab(selected_run, "metrics_v0_3.json", "Rechercheauftraege")
    with tabs[10]:
        _interesting_games_tab(selected_run)
    with tabs[11]:
        _single_game_tab(selected_run)
    with tabs[12]:
        _raw_data_tab(selected_run)
    with tabs[13]:
        _export_tab(selected_run)


def _apply_dashboard_style() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.6rem;
            padding-bottom: 2.5rem;
        }
        .wsim-hero {
            border: 1px solid #d8e0ea;
            border-radius: 8px;
            padding: 1.15rem 1.35rem;
            background: linear-gradient(135deg, #f8fbff 0%, #f5f1e8 100%);
            margin-bottom: 1rem;
        }
        .wsim-hero h1 {
            margin: 0 0 .35rem 0;
            font-size: 2.1rem;
            letter-spacing: 0;
        }
        .wsim-hero p {
            margin: 0;
            color: #3b4856;
            font-size: 1.02rem;
        }
        .wsim-run-pill {
            display: inline-block;
            padding: .22rem .55rem;
            border-radius: 6px;
            background: #17212b;
            color: white;
            font-size: .85rem;
            margin-top: .65rem;
        }
        div[data-testid="stMetric"] {
            border: 1px solid #dfe7ef;
            border-radius: 8px;
            padding: .75rem .85rem;
            background: #ffffff;
        }
        div[data-testid="stTabs"] button {
            font-weight: 600;
        }
        .wsim-section-note {
            color: #526273;
            margin-top: -.35rem;
            margin-bottom: .85rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _dashboard_header(selected_run: Path | None) -> None:
    run_label = selected_run.name if selected_run is not None else "kein Run geladen"
    st.markdown(
        f"""
        <div class="wsim-hero">
          <h1>weltanschauung-sim</h1>
          <p>Lokales Simulationslabor fuer v0.3: Spiele starten, Balancing-Signale lesen und Einzelspiele nachvollziehen.</p>
          <span class="wsim-run-pill">Aktueller Run: {run_label}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _run_selector() -> Path | None:
    runs = list_run_dirs()
    with st.sidebar:
        st.header("Simulationen")
        if not runs:
            st.info("Noch keine Simulationsergebnisse in outputs/runs vorhanden.")
            return None
        labels = [run.name for run in runs]
        preferred = st.session_state.get("selected_run_name")
        default_index = labels.index(preferred) if preferred in labels else 0
        selected = st.selectbox("Vorhandener Run", labels, index=default_index, key="run_selector")
        st.session_state["selected_run_name"] = selected
        st.caption(f"Speicherort: {OUTPUTS_DIR}")
        return runs[labels.index(selected)]


def _start_tab() -> None:
    st.subheader("Simulation starten")
    st.markdown(
        '<p class="wsim-section-note">Die aktuellen v0.3-Dateien sind vorausgewaehlt. Fuer den ersten Lauf reichen 10 bis 100 Spiele; fuer Balancing spaeter deutlich mehr.</p>',
        unsafe_allow_html=True,
    )
    if "dashboard_run_notice" in st.session_state:
        st.success(st.session_state.pop("dashboard_run_notice"))
    defaults = default_config_paths()
    with st.expander("Regeln, Karten und Bots", expanded=True):
        rules_path = _path_select("Regeldatei", defaults["rules"].parent, defaults["rules"])
        cards_path = _path_select("Kartendatei", defaults["cards"].parent, defaults["cards"])
        bots_path = _path_select("Botdatei", defaults["bots"].parent, defaults["bots"])

    col_games, col_seed = st.columns(2)
    with col_games:
        games = st.number_input("Anzahl Spiele", min_value=1, max_value=1_000_000, value=DEFAULT_DASHBOARD_GAMES, step=10)
        st.caption("Klein pruefen, gross auswerten: dieselben Regeln funktionieren fuer beides.")
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
            st.session_state["selected_run_name"] = result.output_dir.name
            st.session_state["dashboard_run_notice"] = f"Simulation fertig: {result.output_dir.name}"
            st.rerun()
        except Exception as exc:  # pragma: no cover - Streamlit displays the user-facing message.
            st.error(f"Simulation konnte nicht gestartet werden: {exc}")


def _overview_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    try:
        metrics = ensure_run_report(run_dir)
        overview = metrics.get("overview", {})
        st.subheader("Ueberblick")
        st.markdown('<p class="wsim-section-note">Die wichtigsten Signale aus dem aktuellen Simulationslauf.</p>', unsafe_allow_html=True)
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Spiele", overview.get("game_count", 0))
        col_b.metric("Durchschnittliche Runden", _fmt(overview.get("rounds", {}).get("average")))
        col_c.metric("Saboteur-Siegquote", _fmt_pct(overview.get("saboteur_win_rate")))
        col_d.metric("Warnungen", len(metrics.get("warnings", [])))
        _warnings_panel(metrics.get("warnings", []))
        _winrate_panel(overview)
        _population_panel(overview)
        with st.expander("Vollstaendigen Markdown-Report anzeigen"):
            st.markdown(read_report(run_dir))
    except Exception as exc:
        st.error(f"Run konnte nicht geladen werden: {exc}")


def _last_games_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    st.subheader("Letzte Spiele")
    st.caption("Die letzten bis zu 1000 Spiele dieses Runs.")
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
        data = _load_json(path)
        _metric_summary(title, data)
        with st.expander("Details als Rohdaten anzeigen"):
            st.json(data)
    except Exception as exc:
        st.error(f"{title} konnten nicht geladen werden: {exc}")


def _interesting_games_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    st.subheader("Auffaellige Spiele")
    st.caption("Schnelle Auswahl fuer Spiele, die sich als Einzelspiel-Analyse lohnen koennen.")
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
    st.subheader("Einzelspiel ansehen")
    st.caption("Hier kann ein gespeichertes Spiel als Rundenprotokoll betrachtet oder exportiert werden.")
    options = game_ids(run_dir)
    if not options:
        st.info("Keine Spiele in diesem Run gefunden.")
        return
    selected = st.selectbox("Spiel auswaehlen", options)
    analysis = st.checkbox("Analysemodus mit geheimen Informationen", value=True)
    if st.button("Spiel anzeigen", type="primary"):
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
    st.subheader("Rohdaten")
    st.caption("Fuer Kontrolle, Export und Detailpruefung.")
    table_name = st.selectbox("Datensatz", ["game_summaries", "round_summaries", "bot_metrics"])
    st.dataframe(read_table(run_dir, table_name), use_container_width=True)


def _export_tab(run_dir: Path | None) -> None:
    if not _require_run(run_dir):
        return
    st.subheader("Export fuer ChatGPT")
    st.write("Erzeugt einen kompakten Analyseordner und eine ZIP-Datei. Diese Datei kann direkt an ChatGPT gegeben werden.")
    if st.button("Analysepaket fuer ChatGPT exportieren", type="primary"):
        try:
            st.success(f"Analysepaket erstellt: {export_chatgpt_package(run_dir)}")
        except Exception as exc:
            st.error(f"Export fehlgeschlagen: {exc}")


def _path_select(label: str, directory: Path, default_path: Path) -> Path:
    files = list_yaml_files(directory)
    labels = [file.name for file in files]
    if not labels:
        raise ValueError(f"Keine YAML-Dateien gefunden in: {directory}")
    default_index = labels.index(default_path.name) if default_path.name in labels else 0
    selected = st.selectbox(label, labels, index=default_index)
    return files[labels.index(selected)]


def _require_run(run_dir: Path | None) -> bool:
    if run_dir is None:
        st.info("Bitte zuerst eine Simulation starten oder einen vorhandenen Run in der Seitenleiste laden.")
        return False
    return True


def _warnings_panel(warnings: list[dict[str, object]]) -> None:
    if not warnings:
        st.success("Keine automatischen Balancing-Warnungen in diesem Run.")
        return
    st.warning(f"{len(warnings)} automatische Balancing-Warnung(en) gefunden.")
    for warning in warnings[:8]:
        st.write(f"- {warning.get('message', 'Warnung')}")
    if len(warnings) > 8:
        st.caption(f"Weitere Warnungen im vollstaendigen Report: {len(warnings) - 8}")


def _winrate_panel(overview: dict[str, object]) -> None:
    col_factions, col_players = st.columns(2)
    with col_factions:
        st.markdown("#### Siegquoten je Fraktion")
        faction_rates = overview.get("faction_win_rates", {}) if isinstance(overview, dict) else {}
        st.dataframe(_mapping_rows(faction_rates, percent=True), use_container_width=True, hide_index=True)
    with col_players:
        st.markdown("#### Siegquoten je Spielerposition")
        player_rates = overview.get("player_position_win_rates", {}) if isinstance(overview, dict) else {}
        st.dataframe(_mapping_rows(player_rates, percent=True), use_container_width=True, hide_index=True)


def _population_panel(overview: dict[str, object]) -> None:
    st.markdown("#### Durchschnittliche Endbevoelkerung")
    final_pop = overview.get("average_final_population_by_faction", {}) if isinstance(overview, dict) else {}
    rows = _mapping_rows(final_pop, percent=False)
    neutral = overview.get("average_final_neutral_population") if isinstance(overview, dict) else None
    if neutral is not None:
        rows.append({"Name": "neutral", "Wert": _fmt(neutral)})
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _metric_summary(title: str, data: dict[str, object]) -> None:
    if title == "Weltgeschichte":
        world = data.get("world_history", {}) if isinstance(data, dict) else {}
        st.write("Wichtig fuer v0.3: Weltgeschichtsreihe, Machtberechnung, Deutungshoheit und aktivierte Propaganda.")
        st.dataframe(_mapping_rows(world.get("interpretation_authority_by_faction", {}) if isinstance(world, dict) else {}), use_container_width=True, hide_index=True)
    elif title == "Kampf":
        combat = data.get("combat", {}) if isinstance(data, dict) else {}
        cols = st.columns(4)
        cols[0].metric("Erfolgreiche Angriffe", combat.get("successful_attacks", 0) if isinstance(combat, dict) else 0)
        cols[1].metric("Ø Kampfwirkung", _fmt(combat.get("average_impact") if isinstance(combat, dict) else None))
        cols[2].metric("Zielmarker", combat.get("target_marker_summaries", 0) if isinstance(combat, dict) else 0)
        cols[3].metric("Ersatzeffekte", combat.get("replacement_effects", 0) if isinstance(combat, dict) else 0)
    elif title == "Rechercheauftraege":
        research = data.get("research_assignments", {}) if isinstance(data, dict) else {}
        cols = st.columns(3)
        cols[0].metric("Erfuellt", research.get("completed", 0) if isinstance(research, dict) else 0)
        cols[1].metric("Abgelegt / getauscht", research.get("discarded_or_redrawn", 0) if isinstance(research, dict) else 0)
        cols[2].metric("Max-Quellen-Samples", research.get("max_sources_reached_samples", 0) if isinstance(research, dict) else 0)
    else:
        st.caption("Zusammenfassung und Details fuer diesen Bereich.")


def _mapping_rows(mapping: object, *, percent: bool = False) -> list[dict[str, str]]:
    if not isinstance(mapping, dict) or not mapping:
        return [{"Name": "Keine Daten", "Wert": ""}]
    return [
        {"Name": str(key), "Wert": _fmt_pct(value) if percent else _fmt(value)}
        for key, value in mapping.items()
    ]


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


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
