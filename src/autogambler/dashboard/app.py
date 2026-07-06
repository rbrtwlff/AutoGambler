from __future__ import annotations

from pathlib import Path

import polars as pl
import streamlit as st


st.set_page_config(page_title="AutoGambler", layout="wide")
st.title("AutoGambler")

outputs_dir = Path("outputs")
summary_files = sorted(outputs_dir.glob("*_summary.csv"), reverse=True)

if not summary_files:
    st.info("Noch keine Simulationsergebnisse gefunden. Starte zuerst eine Simulation ueber die Kommandozeile.")
else:
    selected = st.selectbox("Simulation", summary_files, format_func=lambda path: path.name)
    frame = pl.read_csv(selected)
    st.dataframe(frame, use_container_width=True)
    if "winner_faction_id" in frame.columns:
        winners = frame.group_by("winner_faction_id").len(name="wins").sort("wins", descending=True)
        st.bar_chart(winners, x="winner_faction_id", y="wins")

