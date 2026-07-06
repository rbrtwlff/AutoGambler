from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
import plotly.graph_objects as go
from pydantic import BaseModel

from wsim.core.models import AnalyticsConfig


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
    bot_metrics_frame = _read_table(run_path, "bot_metrics")
    event_rows = _read_event_logs(run_path)
    metadata = _read_metadata(run_path)
    metrics = build_metrics(run_path.name, metadata, game_summaries, round_summaries)
    card_metrics = build_card_metrics(event_rows, game_summaries)
    propaganda_metrics = build_propaganda_metrics(event_rows)
    bot_metrics = build_bot_metrics(event_rows, game_summaries, bot_metrics_frame)
    metrics["cards"] = card_metrics
    metrics["propaganda_advanced"] = propaganda_metrics
    metrics["bots"] = bot_metrics
    metrics["warnings"] = build_balancing_warnings(metrics, metadata.get("analytics"))
    chart_files = _generate_charts_from_data(run_path, metrics, game_summaries, round_summaries)
    metrics["charts"] = chart_files

    metrics_path = run_path / "metrics.json"
    report_path = run_path / "report.md"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    (run_path / "metrics_cards.json").write_text(json.dumps(card_metrics, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    (run_path / "metrics_propaganda.json").write_text(
        json.dumps(propaganda_metrics, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (run_path / "metrics_bots.json").write_text(json.dumps(bot_metrics, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    report_path.write_text(render_markdown_report(metrics), encoding="utf-8")
    return metrics


def generate_charts(run_dir: str | Path) -> list[str]:
    run_path = Path(run_dir)
    if not run_path.exists() or not run_path.is_dir():
        raise ValueError(f"Run directory does not exist: {run_path}")

    game_summaries = _read_table(run_path, "game_summaries")
    round_summaries = _read_table(run_path, "round_summaries")
    bot_metrics_frame = _read_table(run_path, "bot_metrics")
    event_rows = _read_event_logs(run_path)
    metadata = _read_metadata(run_path)
    metrics = build_metrics(run_path.name, metadata, game_summaries, round_summaries)
    metrics["cards"] = build_card_metrics(event_rows, game_summaries)
    metrics["propaganda_advanced"] = build_propaganda_metrics(event_rows)
    metrics["bots"] = build_bot_metrics(event_rows, game_summaries, bot_metrics_frame)
    metrics["warnings"] = build_balancing_warnings(metrics, metadata.get("analytics"))
    chart_files = _generate_charts_from_data(run_path, metrics, game_summaries, round_summaries)
    metrics["charts"] = chart_files

    (run_path / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (run_path / "report.md").write_text(render_markdown_report(metrics), encoding="utf-8")
    return chart_files


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
            "Plotly chart HTML files are written with embedded JavaScript for offline viewing.",
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
        "## WARNINGS",
        _markdown_warnings(metrics.get("warnings", [])),
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
        "## Grafiken",
        _markdown_chart_links(metrics.get("charts", [])),
        "",
        "## Kartenanalyse",
        _markdown_top_metric(metrics.get("cards", {}).get("cards", {}), "swing_value"),
        "",
        "## Propagandaanalyse",
        f"- Propagandawechsel je Spiel: {json.dumps(metrics.get('propaganda_advanced', {}).get('propaganda_switches_by_game', {}), ensure_ascii=True, sort_keys=True)}",
        f"- Haeufigste Konstellationen: {json.dumps(metrics.get('propaganda_advanced', {}).get('most_common_constellations', []), ensure_ascii=True, sort_keys=True)}",
        "",
        "## Botanalyse",
        _markdown_mapping(metrics.get("bots", {}).get("win_rate_by_bot_type", {}), percent=True),
        f"- Bot-Typ-Metriken: {json.dumps(metrics.get('bots', {}).get('by_bot_type', {}), ensure_ascii=True, sort_keys=True)}",
        "",
        "## Hinweise fuer ChatGPT",
        "- Pruefe Balancing-Signale: dauerhaft dominante Fraktionen, hohe Siegquoten einzelner Spielerpositionen, extreme Population-Gaps.",
        "- Fuehrungswechsel, Comebacks und Swings sind erste Heuristiken und sollten spaeter mit feineren Event-Analysen validiert werden.",
        "- Kleine Runs sind nur Smoke-Signale; belastbare Balancing-Schluesse brauchen groessere Stichproben.",
        "",
    ]
    return "\n".join(lines)


def build_balancing_warnings(
    metrics: dict[str, Any],
    analytics_config: AnalyticsConfig | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    thresholds = _analytics_config(analytics_config)
    overview = metrics.get("overview", {})
    cards = metrics.get("cards", {}).get("cards", {})
    propaganda = metrics.get("propaganda_advanced", {})
    bots = metrics.get("bots", {}).get("by_bot_type", {})
    warnings: list[dict[str, Any]] = []

    def add(code: str, message: str, value: Any, threshold: Any) -> None:
        warnings.append(
            {
                "level": "WARNING",
                "code": code,
                "message": f"WARNING: {message}",
                "value": value,
                "threshold": threshold,
            }
        )

    for faction_id, win_rate in overview.get("faction_win_rates", {}).items():
        if win_rate > thresholds.faction_winrate_max:
            add(
                "faction_winrate_high",
                f"{str(faction_id).title()} win rate is {_fmt_pct(win_rate)}, threshold is {_fmt_pct(thresholds.faction_winrate_max)}.",
                win_rate,
                thresholds.faction_winrate_max,
            )
        if win_rate < thresholds.faction_winrate_min:
            add(
                "faction_winrate_low",
                f"{str(faction_id).title()} wins only {_fmt_pct(win_rate)}, expected minimum is {_fmt_pct(thresholds.faction_winrate_min)}.",
                win_rate,
                thresholds.faction_winrate_min,
            )

    saboteur_rate = overview.get("saboteur_win_rate", 0.0)
    if saboteur_rate > thresholds.saboteur_winrate_max:
        add(
            "saboteur_winrate_high",
            f"Saboteur win rate is {_fmt_pct(saboteur_rate)}, threshold is {_fmt_pct(thresholds.saboteur_winrate_max)}.",
            saboteur_rate,
            thresholds.saboteur_winrate_max,
        )

    for player_id, win_rate in overview.get("player_position_win_rates", {}).items():
        if win_rate > thresholds.player_position_advantage_max:
            add(
                "player_position_advantage",
                f"Player {player_id} wins {_fmt_pct(win_rate)}, possible start-player advantage.",
                win_rate,
                thresholds.player_position_advantage_max,
            )

    average_rounds = _to_float(overview.get("rounds", {}).get("average"))
    if average_rounds is not None and average_rounds < thresholds.average_rounds_min:
        add(
            "game_ends_too_early",
            f"Average game length is {_fmt(average_rounds)} rounds, minimum target is {_fmt(thresholds.average_rounds_min)}.",
            average_rounds,
            thresholds.average_rounds_min,
        )
    if average_rounds is not None and average_rounds > thresholds.average_rounds_max:
        add(
            "game_takes_too_long",
            f"Average game length is {_fmt(average_rounds)} rounds, maximum target is {_fmt(thresholds.average_rounds_max)}.",
            average_rounds,
            thresholds.average_rounds_max,
        )

    round_values = overview.get("rounds", {}).get("values", [])
    early_rate = (
        _rate(sum(1 for value in round_values if value < thresholds.average_rounds_min), len(round_values))
        if round_values
        else _to_float(overview.get("rounds", {}).get("early_decision_rate"))
    )
    if early_rate is not None and early_rate > thresholds.early_decision_rate_max:
        add(
            "early_decision_rate_high",
            f"Early decision rate is {_fmt_pct(early_rate)}, threshold is {_fmt_pct(thresholds.early_decision_rate_max)}.",
            early_rate,
            thresholds.early_decision_rate_max,
        )

    for card_id, card in cards.items():
        exposures = int(card.get("play_count") or 0) + int(card.get("propaganda_count") or 0)
        if exposures and card.get("ineffectiveness_rate", 0.0) > thresholds.useless_card_rate_max:
            add(
                "card_useless",
                f"Card {card_id} is ineffective in {_fmt_pct(card.get('ineffectiveness_rate'))} of uses, threshold is {_fmt_pct(thresholds.useless_card_rate_max)}.",
                card.get("ineffectiveness_rate"),
                thresholds.useless_card_rate_max,
            )
        if card.get("swing_value", 0.0) > thresholds.overpowered_card_delta_max:
            add(
                "card_overpowered",
                f"Card {card_id} has swing value {_fmt(card.get('swing_value'))}, threshold is {_fmt(thresholds.overpowered_card_delta_max)}.",
                card.get("swing_value"),
                thresholds.overpowered_card_delta_max,
            )

    slot_dominance = _to_float(propaganda.get("dominant_slot_share"))
    if slot_dominance is not None and slot_dominance > thresholds.propaganda_slot_dominance_max:
        add(
            "propaganda_slot_dominance",
            f"Propaganda slot {propaganda.get('dominant_slot')} carries {_fmt_pct(slot_dominance)} of occupied-slot samples, threshold is {_fmt_pct(thresholds.propaganda_slot_dominance_max)}.",
            slot_dominance,
            thresholds.propaganda_slot_dominance_max,
        )

    research_completion_rate = _to_float(metrics.get("cards", {}).get("research_order_completion_rate"))
    research_draw_count = int(metrics.get("cards", {}).get("research_order_draw_count") or 0)
    if research_draw_count and research_completion_rate is not None and research_completion_rate < thresholds.research_order_completion_min:
        add(
            "research_orders_rarely_completed",
            f"Research Orders completion rate is {_fmt_pct(research_completion_rate)}, expected minimum is {_fmt_pct(thresholds.research_order_completion_min)}.",
            research_completion_rate,
            thresholds.research_order_completion_min,
        )

    for bot_type, bot_metric in bots.items():
        fallback_rate = bot_metric.get("fallback_random_decision_rate", 0.0)
        if fallback_rate > thresholds.fallback_decision_rate_max:
            add(
                "bot_fallback_rate_high",
                f"Bot type {bot_type} fallback decision rate is {_fmt_pct(fallback_rate)}, threshold is {_fmt_pct(thresholds.fallback_decision_rate_max)}.",
                fallback_rate,
                thresholds.fallback_decision_rate_max,
            )

    return warnings


def build_card_metrics(events: list[dict[str, Any]], game_summaries: pl.DataFrame) -> dict[str, Any]:
    game_rows = game_summaries.to_dicts()
    game_count = len(game_rows)
    winning_games = {
        row.get("game_index")
        for row in game_rows
        if row.get("winner_type") not in (None, "none", "draw")
    }
    cards: dict[str, dict[str, Any]] = defaultdict(_empty_card_metric)
    played_by_game: dict[str, set[Any]] = defaultdict(set)
    prop_by_game: dict[str, set[Any]] = defaultdict(set)
    research_order_draw_count = 0
    research_order_completion_count = 0

    for event in events:
        payload = event.get("payload", {})
        game_index = event.get("game_index")
        event_type = event.get("event_type")
        if event_type == "card_drawn":
            for card_id in _payload_card_ids(payload):
                cards[card_id]["draw_count"] += 1
                if card_id.startswith("research_order"):
                    research_order_draw_count += 1
        elif event_type == "action_revealed":
            for card_id in payload.get("committed_card_ids", []):
                cards[card_id]["play_count"] += 1
                played_by_game[card_id].add(game_index)
        elif event_type == "card_discarded":
            card_id = payload.get("card_id")
            if card_id:
                cards[card_id]["discard_count"] += 1
        elif event_type == "propaganda_placed":
            card_id = payload.get("card_id")
            if card_id:
                cards[card_id]["propaganda_count"] += 1
                prop_by_game[card_id].add(game_index)
        elif event_type == "effect_triggered":
            card_id = payload.get("card_id")
            if card_id:
                amount = abs(int(payload.get("amount") or payload.get("result", {}).get("amount") or 0))
                cards[card_id]["activation_count"] += 1
                cards[card_id]["effect_amount_total"] += amount
        elif event_type == "population_changed":
            delta = abs(int(payload.get("applied_delta") or 0))
            for card_id in _cards_revealed_in_round(events, event):
                cards[card_id]["population_swing_total"] += delta
        elif event_type == "research_order_completed":
            research_order_completion_count += 1

    for card_id, row in cards.items():
        exposures = row["play_count"] + row["propaganda_count"]
        row["draw_rate"] = _rate(row["draw_count"], game_count)
        row["play_rate"] = _rate(row["play_count"], game_count)
        row["discard_rate"] = _rate(row["discard_count"], max(1, row["draw_count"]))
        row["propaganda_rate"] = _rate(row["propaganda_count"], game_count)
        row["activation_rate"] = _rate(row["activation_count"], max(1, exposures))
        row["ineffectiveness_rate"] = 1 - row["activation_rate"] if exposures else 0.0
        row["average_effect"] = _rate(row["effect_amount_total"], row["activation_count"])
        row["win_rate_when_played"] = _rate(len(played_by_game[card_id].intersection(winning_games)), len(played_by_game[card_id]))
        row["win_rate_when_propaganda"] = _rate(len(prop_by_game[card_id].intersection(winning_games)), len(prop_by_game[card_id]))
        row["swing_value"] = row["population_swing_total"] + row["effect_amount_total"]

    return {
        "event_log_available": bool(events),
        "card_count": len(cards),
        "cards": dict(sorted(cards.items())),
        "research_order_draw_count": research_order_draw_count,
        "research_order_completion_count": research_order_completion_count,
        "research_order_completion_rate": _rate(research_order_completion_count, research_order_draw_count),
    }


def build_propaganda_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    placements: dict[tuple[Any, str], list[int]] = defaultdict(list)
    lifetimes: dict[str, list[int]] = defaultdict(list)
    switches_by_game: dict[str, int] = defaultdict(int)
    constellation_counts: dict[str, int] = defaultdict(int)
    constellation_effects: dict[str, int] = defaultdict(int)
    current_constellation_by_game: dict[Any, str] = {}
    slot_counts: dict[int, int] = defaultdict(int)
    slot_triggers = 0
    prop_placements = 0

    for event in events:
        payload = event.get("payload", {})
        game_index = event.get("game_index")
        event_type = event.get("event_type")
        if event_type == "propaganda_placed":
            card_id = payload.get("card_id")
            if card_id:
                prop_placements += 1
                placements[(game_index, card_id)].append(int(event.get("round") or 0))
                switches_by_game[str(game_index)] += 1
            constellation = _constellation(payload.get("slots", []))
            current_constellation_by_game[game_index] = constellation
            constellation_counts[constellation] += 1
            _count_occupied_slots(payload.get("slots", []), slot_counts)
        elif event_type == "propaganda_removed":
            card_id = payload.get("card_id")
            if card_id and placements[(game_index, card_id)]:
                start_round = placements[(game_index, card_id)].pop(0)
                lifetimes[card_id].append(max(0, int(event.get("round") or 0) - start_round))
                switches_by_game[str(game_index)] += 1
            constellation = _constellation(payload.get("slots", []))
            current_constellation_by_game[game_index] = constellation
            constellation_counts[constellation] += 1
            _count_occupied_slots(payload.get("slots", []), slot_counts)
        elif event_type == "effect_triggered":
            if payload.get("trigger") == "when_in_propaganda_before_action_resolution":
                slot_triggers += 1
                constellation_effects[current_constellation_by_game.get(game_index, "")] += abs(int(payload.get("amount") or 0))

    average_lifetime = {card_id: _average(values) for card_id, values in lifetimes.items()}
    sorted_constellations = sorted(constellation_counts.items(), key=lambda item: item[1], reverse=True)
    strongest = sorted(constellation_effects.items(), key=lambda item: item[1], reverse=True)
    ineffective = [
        {"constellation": constellation, "count": count}
        for constellation, count in sorted_constellations
        if constellation_effects.get(constellation, 0) == 0
    ][:10]
    slot_total = sum(slot_counts.values())
    dominant_slot = max(slot_counts, key=slot_counts.get) if slot_counts else None
    return {
        "event_log_available": bool(events),
        "average_lifetime_by_card": average_lifetime,
        "slot_trigger_rate": _rate(slot_triggers, prop_placements),
        "slot_occupancy_counts": dict(sorted(slot_counts.items())),
        "dominant_slot": dominant_slot,
        "dominant_slot_share": _rate(slot_counts.get(dominant_slot, 0), slot_total) if dominant_slot is not None else 0.0,
        "most_common_constellations": [{"constellation": key, "count": value} for key, value in sorted_constellations[:10]],
        "strongest_constellations": [{"constellation": key, "effect_total": value} for key, value in strongest[:10]],
        "least_effective_constellations": ineffective,
        "propaganda_switches_by_game": dict(switches_by_game),
    }


def build_bot_metrics(events: list[dict[str, Any]], game_summaries: pl.DataFrame, bot_metrics_frame: pl.DataFrame) -> dict[str, Any]:
    rows = bot_metrics_frame.to_dicts()
    game_rows = game_summaries.to_dicts()
    winner_players_by_game: dict[Any, set[str]] = {}
    for game in game_rows:
        winner_players_by_game[game.get("game_index")] = set(_split_winners(game.get("winner_player")))

    by_type: dict[str, dict[str, Any]] = defaultdict(_empty_bot_type_metric)
    for row in rows:
        bot_type = row.get("bot_type") or "unknown"
        metric = by_type[bot_type]
        metric["rows"] += 1
        metric["attacks"] += int(row.get("attacks") or 0)
        metric["supports"] += int(row.get("supports") or 0)
        metric["population_damage"] += int(row.get("population_damage") or 0)
        metric["population_benefit"] += int(row.get("population_benefit") or 0)
        metric["wins"] += int(row.get("player_id") in winner_players_by_game.get(row.get("game_index"), set()))
        metric["games"] += 1

    decision_counts: dict[str, int] = defaultdict(int)
    fallback_counts: dict[str, int] = defaultdict(int)
    direct_counts: dict[str, int] = defaultdict(int)
    disguised_counts: dict[str, int] = defaultdict(int)
    secrets_by_game_player = _secret_factions_by_game_player(events)
    for event in events:
        if event.get("event_type") != "action_committed":
            continue
        payload = event.get("payload", {})
        bot_type = _bot_type_from_reason(payload.get("action_type_reason", ""))
        decision_counts[bot_type] += 1
        reason_blob = json.dumps(payload, ensure_ascii=True)
        if "RandomBot" in reason_blob or "illegal" in reason_blob or "fallback" in reason_blob:
            fallback_counts[bot_type] += 1
        secret = secrets_by_game_player.get((event.get("game_index"), payload.get("player_id")))
        if secret and payload.get("target_faction_id") == secret:
            direct_counts[bot_type] += 1
        elif secret:
            disguised_counts[bot_type] += 1

    for bot_type, metric in by_type.items():
        actions = metric["attacks"] + metric["supports"]
        metric["attack_support_ratio"] = metric["attacks"] / metric["supports"] if metric["supports"] else float(metric["attacks"])
        metric["average_damage"] = _rate(metric["population_damage"], metric["rows"])
        metric["average_support"] = _rate(metric["population_benefit"], metric["rows"])
        metric["win_rate"] = _rate(metric["wins"], metric["games"])
        metric["directness_score"] = _rate(direct_counts[bot_type], decision_counts[bot_type])
        metric["secrecy_score"] = _rate(disguised_counts[bot_type], decision_counts[bot_type])
        metric["fallback_random_decision_rate"] = _rate(fallback_counts[bot_type], decision_counts[bot_type])
        metric["action_count"] = actions

    return {
        "event_log_available": bool(events),
        "bot_metrics_available": bool(rows),
        "win_rate_by_bot_type": {bot_type: metric["win_rate"] for bot_type, metric in by_type.items()},
        "by_bot_type": dict(sorted(by_type.items())),
    }


def _generate_charts_from_data(
    run_path: Path,
    metrics: dict[str, Any],
    game_summaries: pl.DataFrame,
    round_summaries: pl.DataFrame,
) -> list[str]:
    charts_dir = run_path / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    chart_files: list[str] = []

    overview = metrics["overview"]
    population = metrics["population"]
    faction_ids = list(overview["faction_win_rates"].keys())
    game_rows = game_summaries.to_dicts()
    round_rows = round_summaries.to_dicts()

    chart_files.append(
        _write_chart(
            charts_dir / "winrates_by_faction.html",
            _bar_chart(
                "Siegquoten je Fraktion",
                list(overview["faction_win_rates"].keys()),
                [value * 100 for value in overview["faction_win_rates"].values()],
                "Siegquote (%)",
            ),
        )
    )
    chart_files.append(
        _write_chart(
            charts_dir / "winrates_by_player_position.html",
            _bar_chart(
                "Siegquoten je Spielerposition",
                list(overview["player_position_win_rates"].keys()),
                [value * 100 for value in overview["player_position_win_rates"].values()],
                "Siegquote (%)",
            ),
        )
    )
    chart_files.append(
        _write_chart(
            charts_dir / "round_length_histogram.html",
            _histogram_chart("Rundenlaengen", [row.get("rounds_played") for row in game_rows], "Runden"),
        )
    )
    chart_files.append(
        _write_chart(
            charts_dir / "average_population_by_round.html",
            _average_population_chart(population["average_population_by_round"], faction_ids),
        )
    )
    chart_files.append(
        _write_chart(
            charts_dir / "final_population_boxplot.html",
            _final_population_boxplot(game_rows, faction_ids),
        )
    )
    chart_files.append(
        _write_chart(
            charts_dir / "neutral_population_by_round.html",
            _neutral_population_chart(population["average_population_by_round"]),
        )
    )

    saboteur_chart = _saboteur_winrate_by_round_chart(game_rows)
    if saboteur_chart is not None:
        chart_files.append(_write_chart(charts_dir / "saboteur_winrate_by_round.html", saboteur_chart))

    return [f"charts/{Path(path).name}" for path in chart_files]


def _write_chart(path: Path, figure: go.Figure) -> str:
    figure.update_layout(template="plotly_white", margin={"l": 56, "r": 24, "t": 72, "b": 56})
    figure.write_html(path, include_plotlyjs=True, full_html=True)
    return str(path)


def _bar_chart(title: str, labels: list[str], values: list[float], y_title: str) -> go.Figure:
    figure = go.Figure()
    figure.add_bar(x=labels, y=values)
    figure.update_layout(title=title, xaxis_title="", yaxis_title=y_title)
    return figure


def _histogram_chart(title: str, values: list[Any], x_title: str) -> go.Figure:
    numeric = [value for value in values if _to_float(value) is not None]
    figure = go.Figure()
    figure.add_histogram(x=numeric)
    figure.update_layout(title=title, xaxis_title=x_title, yaxis_title="Anzahl Spiele")
    return figure


def _average_population_chart(rows: list[dict[str, Any]], faction_ids: list[str]) -> go.Figure:
    figure = go.Figure()
    rounds = [row["round"] for row in rows]
    for faction_id in faction_ids:
        figure.add_scatter(
            x=rounds,
            y=[row.get(faction_id) for row in rows],
            mode="lines+markers",
            name=faction_id,
        )
    figure.update_layout(
        title="Durchschnittsbevoelkerung je Runde",
        xaxis_title="Runde",
        yaxis_title="Durchschnittliche Bevoelkerung",
    )
    return figure


def _final_population_boxplot(game_rows: list[dict[str, Any]], faction_ids: list[str]) -> go.Figure:
    figure = go.Figure()
    for faction_id in faction_ids:
        values = [row.get(f"{faction_id}_population") for row in game_rows if row.get(f"{faction_id}_population") is not None]
        figure.add_box(y=values, name=faction_id)
    figure.update_layout(title="Endbevoelkerung je Fraktion", xaxis_title="Fraktion", yaxis_title="Bevoelkerung")
    return figure


def _neutral_population_chart(rows: list[dict[str, Any]]) -> go.Figure:
    figure = go.Figure()
    figure.add_scatter(
        x=[row["round"] for row in rows],
        y=[row.get("neutral") for row in rows],
        mode="lines+markers",
        name="neutral",
    )
    figure.update_layout(
        title="Neutraler Pool je Runde",
        xaxis_title="Runde",
        yaxis_title="Durchschnittlicher neutraler Pool",
    )
    return figure


def _saboteur_winrate_by_round_chart(game_rows: list[dict[str, Any]]) -> go.Figure | None:
    if not game_rows or not any(row.get("winner_type") == "saboteur" for row in game_rows):
        return None

    rows_by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in game_rows:
        if row.get("rounds_played") is not None:
            rows_by_round[int(row["rounds_played"])].append(row)

    rounds = sorted(rows_by_round)
    rates = [
        _rate(sum(1 for row in rows_by_round[round_number] if row.get("winner_type") == "saboteur"), len(rows_by_round[round_number]))
        * 100
        for round_number in rounds
    ]
    figure = go.Figure()
    figure.add_scatter(x=rounds, y=rates, mode="lines+markers", name="saboteur")
    figure.update_layout(
        title="Saboteur-Siegquote nach Spielende-Runde",
        xaxis_title="Runden gespielt",
        yaxis_title="Saboteur-Siegquote (%)",
    )
    return figure


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
            "values": rounds,
            "early_decision_rate": _rate(sum(1 for value in rounds if value < AnalyticsConfig().average_rounds_min), len(rounds)),
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


def _read_event_logs(run_path: Path) -> list[dict[str, Any]]:
    event_path = run_path / "event_logs_sample.jsonl"
    if not event_path.exists() or event_path.stat().st_size == 0:
        return []
    events: list[dict[str, Any]] = []
    with event_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _analytics_config(value: AnalyticsConfig | dict[str, Any] | None) -> AnalyticsConfig:
    if isinstance(value, AnalyticsConfig):
        return value
    if isinstance(value, dict):
        return AnalyticsConfig.model_validate(value)
    return AnalyticsConfig()


def _empty_card_metric() -> dict[str, Any]:
    return {
        "draw_count": 0,
        "play_count": 0,
        "discard_count": 0,
        "propaganda_count": 0,
        "activation_count": 0,
        "effect_amount_total": 0,
        "population_swing_total": 0,
    }


def _empty_bot_type_metric() -> dict[str, Any]:
    return {
        "rows": 0,
        "games": 0,
        "wins": 0,
        "attacks": 0,
        "supports": 0,
        "population_damage": 0,
        "population_benefit": 0,
    }


def _payload_card_ids(payload: dict[str, Any]) -> list[str]:
    if payload.get("card_id"):
        return [payload["card_id"]]
    if payload.get("card_ids"):
        return list(payload["card_ids"])
    return []


def _cards_revealed_in_round(events: list[dict[str, Any]], event: dict[str, Any]) -> set[str]:
    game_index = event.get("game_index")
    round_number = event.get("round")
    player_id = event.get("payload", {}).get("player_id")
    cards: set[str] = set()
    for candidate in events:
        if candidate.get("event_type") != "action_revealed":
            continue
        payload = candidate.get("payload", {})
        if candidate.get("game_index") == game_index and candidate.get("round") == round_number and payload.get("player_id") == player_id:
            cards.update(payload.get("committed_card_ids", []))
    return cards


def _count_occupied_slots(slots: Any, slot_counts: dict[int, int]) -> None:
    if not isinstance(slots, list):
        return
    for slot in slots:
        card_id = slot.get("card_id") if isinstance(slot, dict) else None
        position = slot.get("position") if isinstance(slot, dict) else None
        if card_id and position is not None:
            slot_counts[int(position)] += 1


def _constellation(slots: list[Any]) -> str:
    cards = [str(card_id) for card_id in slots if card_id is not None]
    return "|".join(cards) if cards else "empty"


def _secret_factions_by_game_player(events: list[dict[str, Any]]) -> dict[tuple[Any, str], str]:
    mapping: dict[tuple[Any, str], str] = {}
    for event in events:
        if event.get("event_type") != "initial_state_created":
            continue
        game_index = event.get("game_index")
        for player_id, faction_id in event.get("payload", {}).get("secret_faction_by_player", {}).items():
            mapping[(game_index, player_id)] = faction_id
    return mapping


def _bot_type_from_reason(reason: str) -> str:
    for bot_type in ["loyalist", "deceptive", "saboteur", "heuristic"]:
        if f"profile={bot_type}" in reason:
            return bot_type
    if "RandomBot" in reason:
        return "random"
    return "unknown"


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


def _markdown_chart_links(chart_files: list[str]) -> str:
    if not chart_files:
        return "- Keine Grafiken erzeugt"
    return "\n".join(f"- [{Path(chart_file).name}]({chart_file})" for chart_file in chart_files)


def _markdown_warnings(warnings: list[dict[str, Any]]) -> str:
    if not warnings:
        return "- Keine automatischen Balancing-Warnungen."
    return "\n".join(f"- {warning.get('message', 'WARNING')}" for warning in warnings)


def _markdown_top_metric(items: dict[str, dict[str, Any]], metric_name: str) -> str:
    if not items:
        return "- Keine Kartenmetriken verfuegbar"
    top_items = sorted(items.items(), key=lambda item: item[1].get(metric_name, 0), reverse=True)[:10]
    return "\n".join(f"- {card_id}: {metric_name}={_fmt(values.get(metric_name))}" for card_id, values in top_items)
