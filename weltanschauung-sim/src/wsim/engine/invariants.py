from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wsim.core.events import EventType
from wsim.core.models import CardConfig, GameConfig
from wsim.core.state import GameState


class InvariantError(AssertionError):
    """Raised when a game state violates an engine invariant."""

    def __init__(self, message: str, *, game_id: str | None = None, seed: int | None = None) -> None:
        self.game_id = game_id
        self.seed = seed
        super().__init__(message)


def validate_game_state(config: GameConfig, state: GameState, cards: list[CardConfig]) -> None:
    errors: list[str] = []
    _validate_population(config, state, errors)
    _validate_card_locations(state, cards, errors)
    _validate_prop_track(config, state, errors)
    _validate_players(config, state, errors)
    _validate_events(state, errors)
    if errors:
        raise InvariantError("; ".join(errors), game_id=config.game_id, seed=state.seed)


def validate_game_result_matches_events(result: Any, state: GameState) -> None:
    game_ended = [event for event in state.export_events_as_dicts() if event["event_type"] == EventType.GAME_ENDED.value]
    if not game_ended:
        raise InvariantError("GameResult has no matching game_ended event.", game_id=result.game_id, seed=result.seed)
    payload = game_ended[-1]["payload"]
    mismatches = []
    for key in ["ended_by", "winner_type", "winner_player", "winner_faction", "winning_condition"]:
        if payload.get(key) != getattr(result, key):
            mismatches.append(f"{key}: result={getattr(result, key)!r} event={payload.get(key)!r}")
    if mismatches:
        raise InvariantError("GameResult does not match last game_ended event: " + "; ".join(mismatches), game_id=result.game_id, seed=result.seed)


def export_debug_snapshot(
    *,
    config: GameConfig,
    state: GameState,
    output_dir: str | Path,
    game_index: int | None = None,
    error: BaseException | None = None,
) -> Path:
    debug_dir = Path(output_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"game_{game_index:06d}_" if game_index is not None else ""
    state_path = debug_dir / f"{prefix}state.json"
    events_path = debug_dir / f"{prefix}events.jsonl"
    payload = {
        "game_id": config.game_id,
        "seed": state.seed,
        "game_index": game_index,
        "error": str(error) if error else None,
        "state": state.to_analysis_view(),
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str), encoding="utf-8")
    state.event_log.export_jsonl(events_path)
    return debug_dir


def _validate_population(config: GameConfig, state: GameState, errors: list[str]) -> None:
    for faction_id, faction in state.factions.items():
        if faction.population < 0:
            errors.append(f"Population for faction {faction_id} is negative: {faction.population}")
    if state.neutral_population < 0:
        errors.append(f"neutral_population is negative: {state.neutral_population}")
    active_population = state.neutral_population + sum(faction.population for faction in state.factions.values())
    destroyed_population = config.population.total_population - active_population
    if destroyed_population < 0:
        errors.append(
            f"Population total exceeds configured total: active={active_population} configured={config.population.total_population}"
        )
    if destroyed_population > config.population.total_population:
        errors.append(
            f"Destroyed population is implausible: destroyed={destroyed_population} configured={config.population.total_population}"
        )


def _validate_card_locations(state: GameState, cards: list[CardConfig], errors: list[str]) -> None:
    known_cards = {card.id for card in cards}
    locations: dict[str, list[str]] = {}

    def add(card_id: str | None, location: str) -> None:
        if card_id is None or card_id not in known_cards:
            return
        locations.setdefault(card_id, []).append(location)

    for card_id in state.deck.draw_pile:
        add(card_id, "draw_pile")
    for card_id in state.deck.discard_pile:
        add(card_id, "discard_pile")
    for card_id in state.deck.research_order_pool:
        add(card_id, "research_order_pool")
    for card_id in state.deck.disabled_cards:
        add(card_id, "disabled_cards")
    for player_id, player in state.players.items():
        for card_id in player.hand:
            add(card_id, f"{player_id}.hand")
        for card_id in player.hidden_research_orders:
            add(card_id, f"{player_id}.hidden_research_orders")
        for card_id in player.sources:
            add(card_id, f"{player_id}.sources")
    for position, card_id in enumerate(state.propaganda_track.get_slots()):
        add(card_id, f"propaganda[{position}]")

    discard_set = set(state.deck.discard_pile)
    revealed_card_ids = {
        card_id
        for action in state.revealed_actions
        for card_id in action.committed_card_ids
    }
    for player_id, action in state.planned_actions.items():
        for card_id in action.committed_card_ids:
            if card_id not in discard_set and card_id not in revealed_card_ids:
                add(card_id, f"{player_id}.planned_action")
    for action in state.revealed_actions:
        for card_id in action.committed_card_ids:
            if card_id not in discard_set:
                add(card_id, f"{action.player_id}.revealed_action")

    for card_id, card_locations in sorted(locations.items()):
        unique_locations = sorted(set(card_locations))
        if len(card_locations) > 1:
            errors.append(f"Card {card_id} is in multiple locations: {', '.join(unique_locations)}")

    removed_from_game = _cards_removed_from_game_by_event(state)
    for card_id in sorted(known_cards - set(locations) - removed_from_game):
        errors.append(f"Card {card_id} is missing from all known locations without a remove-from-game event")


def _validate_prop_track(config: GameConfig, state: GameState, errors: list[str]) -> None:
    slot_count = len(state.propaganda_track.get_slots())
    if slot_count > config.propaganda.slots:
        errors.append(f"Propaganda slots exceed config: {slot_count} > {config.propaganda.slots}")


def _cards_removed_from_game_by_event(state: GameState) -> set[str]:
    removed: set[str] = set()
    for event in state.export_events_as_dicts():
        payload = event.get("payload", {})
        if payload.get("destination") == "removed_from_game" and payload.get("card_id"):
            removed.add(str(payload["card_id"]))
    return removed


def _validate_players(config: GameConfig, state: GameState, errors: list[str]) -> None:
    max_sources = config.draft.max_sources
    for player_id, player in state.players.items():
        if len(player.sources) > max_sources:
            errors.append(f"Player {player_id} has too many sources: {len(player.sources)} > {max_sources}")


def _validate_events(state: GameState, errors: list[str]) -> None:
    indices = [event.event_index for event in state.event_log.events]
    if indices != sorted(indices):
        errors.append("Event indices are not sorted.")
    if len(indices) != len(set(indices)):
        errors.append("Event indices are not unique.")
    expected = list(range(len(indices)))
    if indices != expected:
        errors.append("Event indices are not contiguous from zero.")
