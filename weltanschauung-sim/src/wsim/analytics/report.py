from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel


class AnalyticsReportResult(BaseModel):
    run_id: str
    run_dir: Path
    metrics_path: Path
    report_path: Path


def generate_report(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir)
    if not run_path.exists() or not run_path.is_dir():
        raise ValueError(f"Run directory does not exist: {run_path}")

    game_summaries = _read_table(run_path, "game_summaries")
    round_summaries = _read_table(run_path, "round_summaries")
    metadata = _read_metadata(run_path)
    metrics = build_metrics(run_path.name, metadata, game_summaries, round_summaries)

    metrics_path = run_path / "metrics.json"
    report_path = run_path / "report.md"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    report_path.write_text(render_markdown_report(metrics), encoding="utf-8")
    return metrics


def build_metrics(
    run_id: str,
    metadata: dict[str, Any],
    game_summaries: pl.DataFrame,
    round_summaries: pl.DataFrame,
) -> dict[str, Any]:
    faction_ids = _population_factions(game_summaries, round_summaries)
    game_rows = game_summaries.to_dicts()
    round_rows = round_summaries.to_dicts()
    game_count = len(game_rows)

    overview = _overview_metrics(game_rows, faction_ids, game_count)
    population = _population_metrics(round_rows, faction_ids)

    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_metadata": metadata,
        "overview": overview,
        "population": population,
        "notes": [
            "Rates are computed over completed game summary rows.",
            "Comebacks are heuristic: a winning faction counts when it trailed the round leader earlier.",
            "Shared faction wins are split equally across the listed winning factions.",
        ],
    }


def render_markdown_report(metrics: dict[str, Any]) -> str:
    overview = metrics["overview"]
    population = metrics["population"]
    lines = [
        f"# Analysepaket: {metrics['run_id']}",
        "",
        "Dieser Report ist fuer eine weitere Analyse mit ChatGPT vorbereitet. Er fasst die aggregierten Ergebnisse eines lokalen Simulationslaufs zusammen.",
        "",
        "## Kontext",
        f"- Run ID: `{metrics['run_id']}`",
        f"- Erzeugt am: `{metrics['generated_at']}`",
        f"- Spiele: {overview['game_count']}",
        "",
        "## Overview",
        f"- Durchschnittliche Rundenzahl: {_fmt(overview['rounds']['average'])}",
        f"- Median Rundenzahl: {_fmt(overview['rounds']['median'])}",
        f"- Min/Max Rundenzahl: {overview['rounds']['min']} / {overview['rounds']['max']}",
        f"- Saboteur-Siegquote: {_fmt_pct(overview['saboteur_win_rate'])}",
        "",
        "### Siegquote je Fraktion",
        _markdown_mapping(overview["faction_win_rates"], percent=True),
        "",
        "### Siegquote je Spielerposition",
        _markdown_mapping(overview["player_position_win_rates"], percent=True),
        "",
        "### Durchschnittliche Endbevoelkerung",
        _markdown_mapping(overview["average_final_population_by_faction"]),
        f"- Neutraler Pool: {_fmt(overview['average_final_neutral_population'])}",
        "",
        "## Population",
        f"- Durchschnittliche Fuehrungswechsel je Spiel: {_fmt(population['average_leader_changes_per_game'])}",
        f"- Groesster Bevoelkerungsswing: {json.dumps(population['largest_population_swing'], ensure_ascii=True, sort_keys=True)}",
        f"- Eliminierungen: {json.dumps(population['eliminations'], ensure_ascii=True, sort_keys=True)}",
        f"- Comebacks: {json.dumps(population['comebacks'], ensure_ascii=True, sort_keys=True)}",
        "",
        "### Durchschnittsbevoelkerung je Runde",
        _markdown_round_population(population["average_population_by_round"]),
        "",
        "## Hinweise fuer ChatGPT",
        "- Pruefe Balancing-Signale: dauerhaft dominante Fraktionen, hohe Siegquoten einzelner Spielerpositionen, extreme Population-Gaps.",
        "- Fuehrungswechsel, Comebacks und Swings sind erste Heuristiken und sollten spaeter mit feineren Event-Analysen validiert werden.",
        "- Kleine Runs sind nur Smoke-Signale; belastbare Balancing-Schluesse brauchen groessere Stichproben.",
        "",
    ]
    return "\n".join(lines)


def _overview_metrics(game_rows: list[dict[str, Any]], faction_ids: list[str], game_count: int) -> dict[str, Any]:
    rounds = [int(row["rounds_played"]) for row in game_rows if row.get("rounds_played") is not None]
    faction_win_scores = dict.fromkeys(faction_ids, 0.0)
    saboteur_wins = 0
    player_position_wins: dict[str, int] = defaultdict(int)

    for row in game_rows:
        winner_type = row.get("winner_type")
        if winner_type == "saboteur":
            saboteur_wins += 1
        elif winner_type == "faction":
            winners = _split_winners(row.get("winner_faction"))
            if winners:
                share = 1 / len(winners)
                for winner in winners:
                    if winner in faction_win_scores:
                        faction_win_scores[winner] += share
        winner_player = row.get("winner_player")
        if winner_player:
            player_position_wins[str(winner_player)] += 1

    average_final_population = {
        faction_id: _average([row.get(f"{faction_id}_population") for row in game_rows])
        for faction_id in faction_ids
    }

    return {
        "game_count": game_count,
        "faction_win_rates": {
            faction_id: _rate(score, game_count) for faction_id, score in faction_win_scores.items()
        },
        "saboteur_win_rate": _rate(saboteur_wins, game_count),
        "player_position_win_rates": {
            player_id: _rate(wins, game_count) for player_id, wins in sorted(player_position_wins.items())
        },
        "rounds": {
            "average": _average(rounds),
            "median": _median(rounds),
            "min": min(rounds) if rounds else None,
            "max": max(rounds) if rounds else None,
        },
        "average_final_population_by_faction": average_final_population,
        "average_final_neutral_population": _average([row.get("neutral_population") for row in game_rows]),
        "known_winner_rate_total": _rate(
            sum(faction_win_scores.values()) + saboteur_wins + sum(1 for row in game_rows if row.get("winner_type") == "draw"),
            game_count,
        ),
    }


def _population_metrics(round_rows: list[dict[str, Any]], faction_ids: list[str]) -> dict[str, Any]:
    return {
        "average_population_by_round": _average_population_by_round(round_rows, faction_ids),
        "leader_changes_by_game": _leader_changes_by_game(round_rows),
        "average_leader_changes_per_game": _average(_leader_changes_by_game(round_rows).values()),
        "largest_population_swing": _largest_population_swing(round_rows, faction_ids),
        "eliminations": _eliminations(round_rows, faction_ids),
        "comebacks": _comebacks(round_rows),
    }


def _average_population_by_round(round_rows: list[dict[str, Any]], faction_ids: list[str]) -> list[dict[str, Any]]:
    by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in round_rows:
        if row.get("round") is not None:
            by_round[int(row["round"])].append(row)

    result = []
    for round_number in sorted(by_round):
        rows = by_round[round_number]
        result.append(
            {
                "round": round_number,
                **{
                    faction_id: _average(row.get(f"{faction_id}_population") for row in rows)
                    for faction_id in faction_ids
                },
                "neutral": _average(row.get("neutral_population") for row in rows),
            }
        )
    return result


def _leader_changes_by_game(round_rows: list[dict[str, Any]]) -> dict[str, int]:
    rows_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in round_rows:
        rows_by_game[str(row.get("game_index", row.get("game_id", "unknown")))].append(row)

    changes = {}
    for game_key, rows in rows_by_game.items():
        sorted_rows = sorted(rows, key=lambda row: int(row.get("round") or 0))
        previous = None
        count = 0
        for row in sorted_rows:
            leader = row.get("leader_faction")
            if previous is not None and leader != previous:
                count += 1
            previous = leader
        changes[game_key] = count
    return changes


def _largest_population_swing(round_rows: list[dict[str, Any]], faction_ids: list[str]) -> dict[str, Any]:
    rows_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in round_rows:
        rows_by_game[str(row.get("game_index", row.get("game_id", "unknown")))].append(row)

    largest = {"game": None, "faction": None, "from_round": None, "to_round": None, "delta": 0}
    for game_key, rows in rows_by_game.items():
        sorted_rows = sorted(rows, key=lambda row: int(row.get("round") or 0))
        for previous, current in zip(sorted_rows, sorted_rows[1:]):
            for faction_id in faction_ids:
                before = _to_float(previous.get(f"{faction_id}_population"))
                after = _to_float(current.get(f"{faction_id}_population"))
                if before is None or after is None:
                    continue
                delta = after - before
                if abs(delta) > abs(largest["delta"]):
                    largest = {
                        "game": game_key,
                        "faction": faction_id,
                        "from_round": previous.get("round"),
                        "to_round": current.get("round"),
                        "delta": delta,
                    }
    return largest


def _eliminations(round_rows: list[dict[str, Any]], faction_ids: list[str]) -> dict[str, Any]:
    counts = dict.fromkeys(faction_ids, 0)
    first_seen: list[dict[str, Any]] = []
    eliminated_pairs = set()
    for row in sorted(round_rows, key=lambda item: (str(item.get("game_index")), int(item.get("round") or 0))):
        game_key = str(row.get("game_index", row.get("game_id", "unknown")))
        for faction_id in faction_ids:
            key = (game_key, faction_id)
            population = _to_float(row.get(f"{faction_id}_population"))
            if population is not None and population <= 0 and key not in eliminated_pairs:
                eliminated_pairs.add(key)
                counts[faction_id] += 1
                first_seen.append({"game": game_key, "round": row.get("round"), "faction": faction_id})
    return {"counts_by_faction": counts, "first_seen": first_seen[:25]}


def _comebacks(round_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in round_rows:
        rows_by_game[str(row.get("game_index", row.get("game_id", "unknown")))].append(row)

    comeback_games = []
    for game_key, rows in rows_by_game.items():
        sorted_rows = sorted(rows, key=lambda row: int(row.get("round") or 0))
        if not sorted_rows:
            continue
        final_leaders = set(_split_winners(sorted_rows[-1].get("leader_faction")))
        if len(final_leaders) != 1:
            continue
        winner = next(iter(final_leaders))
        if any(winner not in _split_winners(row.get("leader_faction")) for row in sorted_rows[:-1]):
            comeback_games.append({"game": game_key, "winner_faction": winner})
    return {"count": len(comeback_games), "games": comeback_games[:25]}


def _read_table(run_path: Path, stem: str) -> pl.DataFrame:
    parquet_path = run_path / f"{stem}.parquet"
    csv_path = run_path / f"{stem}.csv"
    try:
        if parquet_path.exists() and parquet_path.stat().st_size > 0:
            return pl.read_parquet(parquet_path)
        if csv_path.exists() and csv_path.stat().st_size > 0:
            return pl.read_csv(csv_path)
    except pl.exceptions.NoDataError:
        return pl.DataFrame()
    return pl.DataFrame()


def _read_metadata(run_path: Path) -> dict[str, Any]:
    metadata_path = run_path / "run_metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"warning": "run_metadata.json could not be parsed"}


def _population_factions(game_summaries: pl.DataFrame, round_summaries: pl.DataFrame) -> list[str]:
    columns = list(game_summaries.columns) + list(round_summaries.columns)
    faction_ids = {
        column.removesuffix("_population")
        for column in columns
        if column.endswith("_population") and column != "neutral_population"
    }
    return sorted(faction_ids)


def _split_winners(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value)
    if not text or text.lower() == "null":
        return []
    return [part for part in text.split("|") if part]


def _rate(value: float, total: int) -> float:
    return float(value / total) if total else 0.0


def _average(values: Any) -> float | None:
    numeric = [_to_float(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    return float(sum(numeric) / len(numeric)) if numeric else None


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}"


def _fmt_pct(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.1f}%"


def _markdown_mapping(mapping: dict[str, Any], *, percent: bool = False) -> str:
    if not mapping:
        return "- Keine Daten"
    formatter = _fmt_pct if percent else _fmt
    return "\n".join(f"- {key}: {formatter(value)}" for key, value in mapping.items())


def _markdown_round_population(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "- Keine Rundendaten"
    return "\n".join(f"- Runde {row['round']}: {json.dumps(row, ensure_ascii=True, sort_keys=True)}" for row in rows)
