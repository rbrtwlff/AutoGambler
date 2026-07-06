from __future__ import annotations

import json
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
        self.output_dir.mkdir(parents=True, exist_ok=True)
        game_summaries: list[dict[str, Any]] = []
        round_summaries: list[dict[str, Any]] = []
        event_samples: list[dict[str, Any]] = []
        iterable = range(self.games)
        if self.show_progress:
            iterable = track(iterable, total=self.games, description="Simulating games")

        for game_index in iterable:
            seed = self._subseed(game_index)
            engine = GameEngine(self.rules, self.cards, seed=seed, bots=self.bots)
            result = engine.run_game()
            game_summaries.append(self._game_summary(game_index, seed, result))
            round_summaries.extend(self._round_summaries(game_index, seed, engine))
            if self._should_sample_events(game_index):
                event_samples.extend(self._event_sample_rows(game_index, seed, engine))

        self._write_outputs(game_summaries, round_summaries, event_samples)
        return BatchRunResult(
            run_id=self.run_id,
            output_dir=self.output_dir,
            games=self.games,
            master_seed=self.master_seed,
            game_summary_count=len(game_summaries),
            round_summary_count=len(round_summaries),
            event_sample_count=len(event_samples),
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
            if event["payload"].get("checked_timing") != "end_of_round":
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
            "leader_faction": "|".join(leader_factions),
            "population_gap": population_gap,
            "attacks_this_round": attacks,
            "supports_this_round": supports,
            "cards_played_this_round": cards_played,
            "propaganda_slots": json.dumps(propaganda_slots),
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

    def _should_sample_events(self, game_index: int) -> bool:
        if self.rules.analytics.save_all_events:
            return True
        return game_index >= max(0, self.games - self.rules.analytics.save_last_n_games_events)

    def _event_sample_rows(self, game_index: int, seed: int, engine: GameEngine) -> list[dict[str, Any]]:
        return [
            {"run_id": self.run_id, "game_index": game_index, "seed": seed, **event}
            for event in engine.state.export_events_as_dicts()
        ]

    def _write_outputs(
        self,
        game_summaries: list[dict[str, Any]],
        round_summaries: list[dict[str, Any]],
        event_samples: list[dict[str, Any]],
    ) -> None:
        game_frame = pl.DataFrame(game_summaries)
        round_frame = pl.DataFrame(round_summaries)
        game_frame.write_parquet(self.output_dir / "game_summaries.parquet")
        game_frame.write_csv(self.output_dir / "game_summaries.csv")
        round_frame.write_parquet(self.output_dir / "round_summaries.parquet")
        round_frame.write_csv(self.output_dir / "round_summaries.csv")
        with (self.output_dir / "event_logs_sample.jsonl").open("w", encoding="utf-8") as file:
            for event in event_samples:
                file.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")
        metadata = {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "game_id": self.rules.game_id,
            "games": self.games,
            "master_seed": self.master_seed,
            "analytics": self.rules.analytics.model_dump(),
            "source_paths": self.source_paths,
            "outputs": [
                "run_metadata.json",
                "game_summaries.parquet",
                "game_summaries.csv",
                "round_summaries.parquet",
                "round_summaries.csv",
                "event_logs_sample.jsonl",
            ],
        }
        (self.output_dir / "run_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
