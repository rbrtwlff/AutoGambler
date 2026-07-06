from __future__ import annotations

import polars as pl

from autogambler.engine import SimulationResult


def summarize_winners(results: list[SimulationResult]) -> pl.DataFrame:
    rows = [{"winner_faction_id": result.winner_faction_id} for result in results]
    if not rows:
        return pl.DataFrame({"winner_faction_id": [], "wins": []})
    return (
        pl.DataFrame(rows)
        .group_by("winner_faction_id")
        .len(name="wins")
        .sort("wins", descending=True)
    )

