from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import polars as pl


class GameInspectError(ValueError):
    """Raised when a single game cannot be found or exported."""


def inspect_game(run_dir: str | Path, game_id: str | int, *, analysis_mode: bool = False) -> dict[str, Any]:
    run_path = Path(run_dir)
    if not run_path.exists() or not run_path.is_dir():
        raise GameInspectError(f"Run directory does not exist: {run_path}")

    metadata = _read_metadata(run_path)
    game_summaries = _read_table(run_path, "game_summaries")
    round_summaries = _read_table(run_path, "round_summaries")
    if game_summaries.is_empty():
        raise GameInspectError(f"No game summaries found in run: {run_path}")

    game_row = _find_game_row(game_summaries, game_id)
    game_index = int(game_row["game_index"]) if game_row.get("game_index") is not None else None
    matched_game_id = str(game_row.get("game_id", game_id))
    round_rows = _filter_round_rows(round_summaries, game_row, game_id)
    events = _read_game_events(run_path, game_row, game_id)

    setup = _setup_section(events, metadata, analysis_mode)
    rounds = _round_sections(round_rows, events)
    victory = _victory_section(game_row, events)

    return {
        "run_id": run_path.name,
        "requested_game_id": str(game_id),
        "game_index": game_index,
        "game_id": matched_game_id,
        "metadata": {
            "seed": game_row.get("seed"),
            "rounds_played": game_row.get("rounds_played"),
            "event_count": game_row.get("event_count"),
            "source_paths": metadata.get("source_paths", {}),
            "run_metadata": metadata,
        },
        "players": setup["players"],
        "roles": setup["roles"],
        "secret_factions": setup["secret_factions"],
        "start_state": setup["start_state"],
        "rounds": rounds,
        "effects": _events_by_type(events, "effect_triggered"),
        "warnings": _events_by_type(events, "warning"),
        "victory": victory,
        "analysis_mode": analysis_mode,
    }


def export_game(
    run_dir: str | Path,
    game_id: str | int,
    *,
    export_format: Literal["markdown", "json"],
    analysis_mode: bool = True,
) -> Path:
    report = inspect_game(run_dir, game_id, analysis_mode=analysis_mode)
    games_dir = Path(run_dir) / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    file_stem = _game_file_stem(report)

    if export_format == "markdown":
        output_path = games_dir / f"{file_stem}.md"
        output_path.write_text(render_game_markdown(report), encoding="utf-8")
        return output_path
    if export_format == "json":
        output_path = games_dir / f"{file_stem}.json"
        output_path.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return output_path
    raise GameInspectError(f"Unsupported export format: {export_format}")


def render_game_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    victory = report["victory"]
    lines = [
        f"# Einzelspiel-Analyse: {report['run_id']} / Spiel {report['game_index']}",
        "",
        "Dieser Export ist fuer eine direkte Analyse mit ChatGPT vorbereitet. Er enthaelt den Spielverlauf, soweit er aus den gespeicherten Summary- und Eventdaten rekonstruierbar ist.",
        "",
        "## Metadaten",
        f"- Run ID: `{report['run_id']}`",
        f"- Game Index: {report['game_index']}",
        f"- Game ID: `{report['game_id']}`",
        f"- Seed: {metadata['seed']}",
        f"- Runden gespielt: {metadata['rounds_played']}",
        f"- Event Count: {metadata['event_count']}",
        f"- Analysemodus: {report['analysis_mode']}",
        "",
        "## Quellen",
        _markdown_mapping(metadata.get("source_paths") or {"Hinweis": "Dateipfade wurden fuer diesen Run nicht gespeichert."}),
        "",
        "## Spielerpositionen",
        _markdown_players(report["players"]),
        "",
        "## Rollen",
        _markdown_mapping(report["roles"]),
        "",
        "## Geheime Fraktionen",
        _markdown_mapping(report["secret_factions"]),
        "",
        "## Startzustand",
        _markdown_mapping(report["start_state"]),
        "",
        "## Runde-fuer-Runde-Zusammenfassung",
        _markdown_rounds(report["rounds"]),
        "",
        "## Ausgeloeste Effekte",
        _markdown_events(report["effects"]),
        "",
        "## Siegbedingung",
        f"- Winner Type: {victory.get('winner_type')}",
        f"- Winner Player: {victory.get('winner_player')}",
        f"- Winner Faction: {victory.get('winner_faction')}",
        f"- Winning Condition: {victory.get('winning_condition')}",
        f"- Ended By: {victory.get('ended_by')}",
        f"- Tie Info: {json.dumps(victory.get('tie_info'), ensure_ascii=True, sort_keys=True)}",
        "",
    ]
    return "\n".join(lines)


def _setup_section(events: list[dict[str, Any]], metadata: dict[str, Any], analysis_mode: bool) -> dict[str, Any]:
    game_started = _first_event(events, "game_started")
    initial_state = _first_event(events, "initial_state_created")
    start_payload = game_started.get("payload", {}) if game_started else {}
    setup_payload = initial_state.get("payload", {}) if initial_state else {}
    player_ids = start_payload.get("player_ids", [])

    roles = {
        "start_player": setup_payload.get("start_player_id"),
        "journalist": setup_payload.get("journalist_player_id"),
        "media_mogul": setup_payload.get("media_mogul_player_id"),
        "saboteurs": setup_payload.get("saboteur_player_ids", []),
    }
    secret_factions = setup_payload.get("secret_faction_by_player") if analysis_mode else None

    return {
        "players": [
            {"player_id": player_id, "position": index + 1}
            for index, player_id in enumerate(player_ids)
        ],
        "roles": roles,
        "secret_factions": secret_factions or {"Hinweis": "Nur im Analysemodus verfuegbar oder im Run nicht gespeichert."},
        "start_state": {
            "seed": start_payload.get("seed") or metadata.get("master_seed"),
            "factions": start_payload.get("faction_ids", []),
            "total_population": start_payload.get("total_population"),
            "faction_population": setup_payload.get("faction_population", {}),
            "neutral_population": setup_payload.get("neutral_population"),
            "hand_size": setup_payload.get("hand_size"),
            "hidden_research_orders": setup_payload.get("hidden_research_orders"),
            "remaining_draw_pile": setup_payload.get("remaining_draw_pile"),
            "remaining_research_order_pool": setup_payload.get("remaining_research_order_pool"),
        },
    }


def _round_sections(round_rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rounds = []
    events_by_round: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        event_round = int(event.get("round") or 0)
        events_by_round.setdefault(event_round, []).append(event)

    for row in sorted(round_rows, key=lambda item: int(item.get("round") or 0)):
        round_number = int(row.get("round") or 0)
        round_events = events_by_round.get(round_number, [])
        rounds.append(
            {
                "round": round_number,
                "leader_faction": row.get("leader_faction"),
                "population_gap": row.get("population_gap"),
                "populations": _population_columns(row),
                "neutral_population": row.get("neutral_population"),
                "propaganda_slots": _parse_json(row.get("propaganda_slots"), fallback=[]),
                "actions": _round_actions(round_events),
                "draft": _payloads(round_events, {"draft_started", "draft_contributed", "card_drafted", "draft_finished"}),
                "journalist": row.get("journalist_player"),
                "media_mogul": row.get("media_mogul_player"),
                "source_counts": _parse_json(row.get("source_counts"), fallback={}),
                "hand_counts": _parse_json(row.get("hand_counts"), fallback={}),
                "world_history_row": _parse_json(row.get("world_history_row"), fallback=[]),
                "base_power_by_faction": _parse_json(row.get("base_power_by_faction"), fallback={}),
                "activated_propaganda_power_by_faction": _parse_json(row.get("activated_propaganda_power_by_faction"), fallback={}),
                "final_power_by_faction": _parse_json(row.get("final_power_by_faction"), fallback={}),
                "combat_requested_deltas": _parse_json(row.get("combat_requested_deltas"), fallback={}),
                "combat_applied_deltas": _parse_json(row.get("combat_applied_deltas"), fallback={}),
                "victory_checks": _payloads(round_events, {"victory_checked"}),
                "research_assignments": _payloads(round_events, {"research_assignments_checked", "research_order_completed", "research_order_discarded"}),
                "effects": [event["payload"] for event in round_events if event.get("event_type") == "effect_triggered"],
                "attacks_this_round": row.get("attacks_this_round"),
                "supports_this_round": row.get("supports_this_round"),
                "cards_played_this_round": row.get("cards_played_this_round"),
            }
        )
    return rounds


def _round_actions(round_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    revealed_by_player = {
        event["payload"].get("player_id"): event["payload"]
        for event in round_events
        if event.get("event_type") == "action_revealed"
    }
    actions = []
    for event in round_events:
        if event.get("event_type") != "action_resolved":
            continue
        payload = event["payload"]
        player_id = payload.get("player_id")
        revealed = revealed_by_player.get(player_id, {})
        actions.append(
            {
                "player_id": player_id,
                "action_type": payload.get("action_type"),
                "target_faction_id": payload.get("target_faction_id"),
                "strength": payload.get("strength"),
                "applied_delta": payload.get("applied_delta"),
                "population_before": payload.get("population_before"),
                "population_after": payload.get("population_after"),
                "neutral_before": payload.get("neutral_before"),
                "neutral_after": payload.get("neutral_after"),
                "committed_card_ids": revealed.get("committed_card_ids", []),
                "initiative_count": revealed.get("initiative_count"),
                "reveal_order": revealed.get("reveal_order"),
            }
        )
    return actions


def _payloads(round_events: list[dict[str, Any]], event_types: set[str]) -> list[dict[str, Any]]:
    return [
        {"event_type": event.get("event_type"), **(event.get("payload") or {})}
        for event in round_events
        if event.get("event_type") in event_types
    ]


def _victory_section(game_row: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    game_ended = _first_event(reversed(events), "game_ended")
    payload = game_ended.get("payload", {}) if game_ended else {}
    return {
        "ended_by": game_row.get("ended_by") or payload.get("ended_by"),
        "winner_type": game_row.get("winner_type") or payload.get("winner_type"),
        "winner_player": game_row.get("winner_player") or payload.get("winner_player"),
        "winner_faction": game_row.get("winner_faction") or payload.get("winner_faction"),
        "winning_condition": game_row.get("winning_condition") or payload.get("winning_condition"),
        "tie_info": _parse_json(game_row.get("tie_info"), fallback=payload.get("tie_info")),
    }


def _find_game_row(game_summaries: pl.DataFrame, game_id: str | int) -> dict[str, Any]:
    rows = game_summaries.to_dicts()
    requested = str(game_id)
    requested_int = _try_int(requested)
    for row in rows:
        if requested_int is not None and row.get("game_index") is not None and int(row["game_index"]) == requested_int:
            return row
        if str(row.get("game_id")) == requested:
            return row
    available = sorted(str(row.get("game_index")) for row in rows[:10])
    raise GameInspectError(f"Game not found: {game_id}. Try one of these game indexes: {', '.join(available)}")


def _filter_round_rows(round_summaries: pl.DataFrame, game_row: dict[str, Any], game_id: str | int) -> list[dict[str, Any]]:
    if round_summaries.is_empty():
        return []
    rows = round_summaries.to_dicts()
    requested = str(game_id)
    game_index = game_row.get("game_index")
    return [
        row
        for row in rows
        if (game_index is not None and row.get("game_index") == game_index) or str(row.get("game_id")) == requested
    ]


def _read_game_events(run_path: Path, game_row: dict[str, Any], game_id: str | int) -> list[dict[str, Any]]:
    event_path = run_path / "event_logs_sample.jsonl"
    if not event_path.exists():
        return []
    events = []
    requested = str(game_id)
    game_index = game_row.get("game_index")
    with event_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            event = json.loads(line)
            if (game_index is not None and event.get("game_index") == game_index) or str(event.get("game_id")) == requested:
                events.append(event)
    return sorted(events, key=lambda event: int(event.get("event_index") or 0))


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


def _population_columns(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key.removesuffix("_population"): value
        for key, value in row.items()
        if key.endswith("_population") and key != "neutral_population"
    }


def _events_by_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event_type") == event_type]


def _first_event(events: Any, event_type: str) -> dict[str, Any] | None:
    for event in events:
        if event.get("event_type") == event_type:
            return event
    return None


def _parse_json(value: Any, *, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _game_file_stem(report: dict[str, Any]) -> str:
    game_index = report.get("game_index")
    if isinstance(game_index, int):
        return f"game_{game_index:06d}"
    return f"game_{_safe_filename(str(report['requested_game_id']))}"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


def _try_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _markdown_mapping(mapping: dict[str, Any]) -> str:
    if not mapping:
        return "- Keine Daten"
    return "\n".join(f"- {key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}" for key, value in mapping.items())


def _markdown_players(players: list[dict[str, Any]]) -> str:
    if not players:
        return "- Keine Spielerinformationen gespeichert"
    return "\n".join(f"- Position {player['position']}: {player['player_id']}" for player in players)


def _markdown_rounds(rounds: list[dict[str, Any]]) -> str:
    if not rounds:
        return "- Keine Rundendaten gefunden. Pruefe, ob fuer dieses Spiel Event- und Round-Summary-Daten gespeichert wurden."
    lines = []
    for round_info in rounds:
        lines.extend(
            [
                f"### Runde {round_info['round']}",
                f"- Leader: {round_info['leader_faction']}",
                f"- Population Gap: {round_info['population_gap']}",
                f"- Bevoelkerung: {json.dumps(round_info['populations'], ensure_ascii=True, sort_keys=True)}",
                f"- Neutraler Pool: {round_info['neutral_population']}",
                f"- Journalist: {round_info.get('journalist')}",
                f"- Medienmogul: {round_info.get('media_mogul')}",
                f"- Quellen: {json.dumps(round_info.get('source_counts', {}), ensure_ascii=True, sort_keys=True)}",
                f"- Handkarten: {json.dumps(round_info.get('hand_counts', {}), ensure_ascii=True, sort_keys=True)}",
                f"- Draft: {json.dumps(round_info.get('draft', []), ensure_ascii=True, sort_keys=True)}",
                f"- Propagandaleiste: {json.dumps(round_info['propaganda_slots'], ensure_ascii=True)}",
                f"- Urne / Weltgeschichte: {json.dumps(round_info.get('world_history_row', []), ensure_ascii=True)}",
                f"- Basismacht: {json.dumps(round_info.get('base_power_by_faction', {}), ensure_ascii=True, sort_keys=True)}",
                f"- Aktivierte Propagandamacht: {json.dumps(round_info.get('activated_propaganda_power_by_faction', {}), ensure_ascii=True, sort_keys=True)}",
                f"- Finale Macht: {json.dumps(round_info.get('final_power_by_faction', {}), ensure_ascii=True, sort_keys=True)}",
                f"- Stossrichtungen / angeforderte Kampfdeltas: {json.dumps(round_info.get('combat_requested_deltas', {}), ensure_ascii=True, sort_keys=True)}",
                f"- Kampf / angewendete Deltas: {json.dumps(round_info.get('combat_applied_deltas', {}), ensure_ascii=True, sort_keys=True)}",
                f"- Aktionen: {json.dumps(round_info['actions'], ensure_ascii=True, sort_keys=True)}",
                f"- Siegpruefung: {json.dumps(round_info.get('victory_checks', []), ensure_ascii=True, sort_keys=True)}",
                f"- Rechercheauftraege: {json.dumps(round_info.get('research_assignments', []), ensure_ascii=True, sort_keys=True)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _markdown_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "- Keine Effekt-Events gespeichert"
    return "\n".join(f"- {json.dumps(event, ensure_ascii=True, sort_keys=True)}" for event in events)
