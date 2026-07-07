from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel
from rich.progress import track

from wsim.core.models import BotConfig, CardConfig, GameConfig
from wsim.engine.game_engine import GameEngine, GameResult


class BatchRunResult(BaseModel):
    run_id: str
    output_dir: Path
    games: int
    master_seed: int
    game_summary_count: int
    round_summary_count: int
    event_sample_count: int
    total_event_count: int
    elapsed_seconds: float
    games_per_second: float
    output_size_bytes: int


class SimulationBatchRunner:
    def __init__(
        self,
        *,
        rules: GameConfig,
        cards: list[CardConfig],
        bots: list[BotConfig],
        games: int,
        master_seed: int,
        output_dir: Path,
        show_progress: bool = True,
        source_paths: dict[str, str] | None = None,
    ) -> None:
        self.rules = rules
        self.cards = cards
        self.bots = bots
        self.games = games
        self.master_seed = master_seed
        self.output_dir = output_dir
        self.run_id = output_dir.name
        self.show_progress = show_progress
        self.source_paths = source_paths or {}

    def run(self) -> BatchRunResult:
        started_at = time.perf_counter()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        game_summaries: list[dict[str, Any]] = []
        round_summaries: list[dict[str, Any]] = []
        bot_metrics: list[dict[str, Any]] = []
        event_samples: list[dict[str, Any]] = []
        total_event_count = 0
        iterable = range(self.games)
        if self.show_progress:
            iterable = track(iterable, total=self.games, description="Simulating games")

        for game_index in iterable:
            seed = self._subseed(game_index)
            engine = GameEngine(self.rules, self.cards, seed=seed, bots=self.bots)
            result = engine.run_game()
            total_event_count += result.event_count
            game_summaries.append(self._game_summary(game_index, seed, result))
            round_summaries.extend(self._round_summaries(game_index, seed, engine))
            bot_metrics.extend(self._bot_metrics(game_index, seed, engine))
            if self._should_sample_events(game_index):
                event_samples.extend(self._event_sample_rows(game_index, seed, engine))

        elapsed_seconds = time.perf_counter() - started_at
        games_per_second = self.games / elapsed_seconds if elapsed_seconds > 0 else float(self.games)
        self._write_outputs(
            game_summaries,
            round_summaries,
            bot_metrics,
            event_samples,
            total_event_count=total_event_count,
            elapsed_seconds=elapsed_seconds,
            games_per_second=games_per_second,
        )
        output_size_bytes = _directory_size(self.output_dir)
        return BatchRunResult(
            run_id=self.run_id,
            output_dir=self.output_dir,
            games=self.games,
            master_seed=self.master_seed,
            game_summary_count=len(game_summaries),
            round_summary_count=len(round_summaries),
            event_sample_count=len(event_samples),
            total_event_count=total_event_count,
            elapsed_seconds=elapsed_seconds,
            games_per_second=games_per_second,
            output_size_bytes=output_size_bytes,
        )

    def _subseed(self, game_index: int) -> int:
        return self.master_seed + game_index

    def _game_summary(self, game_index: int, seed: int, result: GameResult) -> dict[str, Any]:
        row = {
            "run_id": self.run_id,
            "game_index": game_index,
            "game_id": result.game_id,
            "seed": seed,
            "rounds_played": result.rounds_played,
            "ended_by": result.ended_by,
            "winner_type": result.winner_type,
            "winner_player": result.winner_player,
            "winner_faction": result.winner_faction,
            "winning_condition": result.winning_condition,
            "tie_info": json.dumps(result.tie_info, sort_keys=True) if result.tie_info is not None else None,
            "event_count": result.event_count,
        }
        for faction_id, population in result.final_populations.items():
            row[f"{faction_id}_population"] = population
        return row

    def _round_summaries(self, game_index: int, seed: int, engine: GameEngine) -> list[dict[str, Any]]:
        events = engine.state.export_events_as_dicts()
        summaries: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] != "victory_checked" or event["round"] <= 0:
                continue
            checked_timing = event["payload"].get("checked_timing")
            if checked_timing not in {"end_of_round", "v0_3_victory_check"}:
                continue
            summaries.append(self._round_summary_from_events(game_index, seed, event["round"], events))
        return summaries

    def _round_summary_from_events(
        self,
        game_index: int,
        seed: int,
        round_number: int,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        population_events = [
            event
            for event in events
            if event["round"] <= round_number and event["event_type"] == "population_changed"
        ]
        populations = {faction.id: faction.start_population for faction in self.rules.factions}
        neutral_population = self.rules.population.neutral_start
        for event in population_events:
            payload = event["payload"]
            populations[payload["target_faction_id"]] = payload["population_after"]
            neutral_population = payload["neutral_after"]
        active_population = neutral_population + sum(populations.values())
        destroyed_population = max(0, self.rules.population.total_population - active_population)

        round_events = [event for event in events if event["round"] == round_number]
        attacks = sum(
            1
            for event in round_events
            if event["event_type"] == "action_resolved" and event["payload"]["action_type"] == "attack"
        )
        supports = sum(
            1
            for event in round_events
            if event["event_type"] == "action_resolved" and event["payload"]["action_type"] == "support"
        )
        cards_played = sum(
            len(event["payload"]["committed_card_ids"])
            for event in round_events
            if event["event_type"] == "action_revealed"
        )
        propaganda_slots = self._latest_prop_slots(round_number, events)
        snapshot = _latest_payload(round_events, "round_start_snapshot")
        world_power = _latest_payload(round_events, "world_history_power_calculated")
        combat = _latest_payload(round_events, "combat_resolved")
        world_history = _latest_payload(round_events, "world_history_revealed")
        victory = _latest_payload(round_events, "victory_checked")
        source_counts = {
            player_id: player.get("source_count")
            for player_id, player in (snapshot.get("players") or {}).items()
        }
        hand_counts = {
            player_id: player.get("hand_size")
            for player_id, player in (snapshot.get("players") or {}).items()
        }
        leader_population = max(populations.values())
        leader_factions = [key for key, value in populations.items() if value == leader_population]
        sorted_populations = sorted(populations.values(), reverse=True)
        population_gap = sorted_populations[0] - sorted_populations[1] if len(sorted_populations) > 1 else 0

        row = {
            "run_id": self.run_id,
            "game_index": game_index,
            "game_id": self.rules.game_id,
            "seed": seed,
            "round": round_number,
            "neutral_population": neutral_population,
            "destroyed_population": destroyed_population,
            "leader_faction": "|".join(leader_factions),
            "population_gap": population_gap,
            "attacks_this_round": attacks,
            "supports_this_round": supports,
            "cards_played_this_round": cards_played,
            "propaganda_slots": json.dumps(propaganda_slots),
            "journalist_player": snapshot.get("journalist_player_id"),
            "media_mogul_player": snapshot.get("media_mogul_player_id"),
            "source_counts": json.dumps(source_counts, sort_keys=True),
            "hand_counts": json.dumps(hand_counts, sort_keys=True),
            "world_history_row": json.dumps(world_history.get("card_ids", [])),
            "base_power_by_faction": json.dumps(world_power.get("base_power", {}), sort_keys=True),
            "activated_propaganda_power_by_faction": json.dumps(world_power.get("propaganda_power", {}), sort_keys=True),
            "final_power_by_faction": json.dumps(world_power.get("total_power", {}), sort_keys=True),
            "combat_requested_deltas": json.dumps(combat.get("requested_deltas", {}), sort_keys=True),
            "combat_applied_deltas": json.dumps(combat.get("applied_deltas", {}), sort_keys=True),
            "successful_attacks": sum(1 for value in (combat.get("applied_deltas") or {}).values() if int(value or 0) < 0),
            "neutral_impulses": sum(1 for value in (combat.get("requested_deltas") or {}).values() if int(value or 0) > 0),
            "eliminated_factions": json.dumps(victory.get("eliminated_factions", [])),
            "winning_condition_checked": victory.get("winning_condition"),
            "blocked_tie_info": json.dumps(victory.get("tie_info"), sort_keys=True) if victory.get("tie_info") else "",
        }
        for faction_id, population in populations.items():
            row[f"{faction_id}_population"] = population
        return row

    def _latest_prop_slots(self, round_number: int, events: list[dict[str, Any]]) -> list[str | None]:
        slots: list[str | None] = [None for _ in range(self.rules.propaganda.slots)]
        for event in events:
            if event["round"] > round_number:
                continue
            if event["event_type"] in {"propaganda_placed", "propaganda_removed"}:
                slots = event["payload"]["slots"]
        return slots

    def _bot_metrics(self, game_index: int, seed: int, engine: GameEngine) -> list[dict[str, Any]]:
        events = engine.state.export_events_as_dicts()
        bot_by_player = {bot.player_id: bot for bot in self.bots}
        rows: dict[str, dict[str, Any]] = {}
        for player in self.rules.players:
            bot = bot_by_player.get(player.id)
            rows[player.id] = {
                "run_id": self.run_id,
                "game_index": game_index,
                "game_id": self.rules.game_id,
                "seed": seed,
                "player_id": player.id,
                "bot_id": bot.id if bot else None,
                "bot_type": bot.type if bot else None,
                "attacks": 0,
                "supports": 0,
                "population_damage": 0,
                "population_benefit": 0,
            }

        for event in events:
            if event["event_type"] != "population_changed":
                continue
            payload = event["payload"]
            player_id = payload["player_id"]
            if player_id not in rows:
                continue
            row = rows[player_id]
            if payload["action_type"] == "attack":
                row["attacks"] += 1
                row["population_damage"] += max(0, -int(payload["applied_delta"]))
            elif payload["action_type"] == "support":
                row["supports"] += 1
                row["population_benefit"] += max(0, int(payload["applied_delta"]))

        for row in rows.values():
            total_actions = row["attacks"] + row["supports"]
            row["attack_support_ratio"] = row["attacks"] / row["supports"] if row["supports"] else float(row["attacks"])
            row["attack_rate"] = row["attacks"] / total_actions if total_actions else 0.0
            row["support_rate"] = row["supports"] / total_actions if total_actions else 0.0
        return list(rows.values())

    def _should_sample_events(self, game_index: int) -> bool:
        if self.rules.analytics.save_all_events:
            return True
        if not self.rules.analytics.sampled_event_logging:
            return False
        return game_index >= max(0, self.games - self.rules.analytics.save_last_n_games_events)

    def _event_sample_rows(self, game_index: int, seed: int, engine: GameEngine) -> list[dict[str, Any]]:
        events = engine.state.export_events_as_dicts()
        if self.rules.analytics.minimal_logging:
            events = [event for event in events if event["event_type"] in _minimal_event_types()]
        return [
            {"run_id": self.run_id, "game_index": game_index, "seed": seed, **event}
            for event in events
        ]

    def _write_outputs(
        self,
        game_summaries: list[dict[str, Any]],
        round_summaries: list[dict[str, Any]],
        bot_metrics: list[dict[str, Any]],
        event_samples: list[dict[str, Any]],
        *,
        total_event_count: int,
        elapsed_seconds: float,
        games_per_second: float,
    ) -> None:
        game_frame = pl.DataFrame(game_summaries)
        round_frame = pl.DataFrame(round_summaries)
        bot_frame = pl.DataFrame(bot_metrics)
        game_frame.write_parquet(self.output_dir / "game_summaries.parquet")
        game_frame.write_csv(self.output_dir / "game_summaries.csv")
        round_frame.write_parquet(self.output_dir / "round_summaries.parquet")
        round_frame.write_csv(self.output_dir / "round_summaries.csv")
        bot_frame.write_parquet(self.output_dir / "bot_metrics.parquet")
        bot_frame.write_csv(self.output_dir / "bot_metrics.csv")
        with (self.output_dir / "event_logs_sample.jsonl").open("w", encoding="utf-8") as file:
            for event in event_samples:
                file.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")
        metadata = {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "game_id": self.rules.game_id,
            "games": self.games,
            "master_seed": self.master_seed,
            "performance": {
                "elapsed_seconds": elapsed_seconds,
                "games_per_second": games_per_second,
                "total_event_count": total_event_count,
                "sampled_event_count": len(event_samples),
            },
            "analytics": self.rules.analytics.model_dump(),
            "source_paths": self.source_paths,
            "outputs": [
                "run_metadata.json",
                "game_summaries.parquet",
                "game_summaries.csv",
                "round_summaries.parquet",
                "round_summaries.csv",
                "bot_metrics.parquet",
                "bot_metrics.csv",
                "event_logs_sample.jsonl",
            ],
        }
        (self.output_dir / "run_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        metadata["performance"]["output_size_bytes"] = _directory_size(self.output_dir)
        (self.output_dir / "run_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _minimal_event_types() -> set[str]:
    return {
        "game_started",
        "initial_state_created",
        "round_started",
        "population_changed",
        "victory_checked",
        "game_ended",
        "warning",
    }


def _latest_payload(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            payload = event.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
