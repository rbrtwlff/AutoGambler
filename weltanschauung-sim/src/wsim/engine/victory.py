from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from wsim.core.models import GameConfig, SaboteurWinConditionConfig
from wsim.core.state import GameState


class VictoryResult(BaseModel):
    is_win: bool = False
    winner_type: Literal["faction", "saboteur", "draw", "none"] = "none"
    winner_player: str | None = None
    winner_faction: str | None = None
    winning_condition: str | None = None
    tie_info: dict | None = None
    checked_timing: str
    details: dict = Field(default_factory=dict)


class VictoryChecker:
    def __init__(self, config: GameConfig) -> None:
        self.config = config

    def check(self, state: GameState, timing: str) -> VictoryResult:
        if timing not in self.config.victory.check_timing:
            return VictoryResult(
                checked_timing=timing,
                details={"skipped": True, "reason": "timing_not_configured"},
            )

        saboteur_result = self._check_saboteur(state, timing)
        if saboteur_result.is_win:
            return saboteur_result

        if timing == "game_end":
            return self._check_faction_game_end(state, timing)

        return VictoryResult(checked_timing=timing, details={"checked": True, "winner": None})

    def _check_saboteur(self, state: GameState, timing: str) -> VictoryResult:
        saboteur_players = [player for player in state.players.values() if player.is_saboteur]
        if not saboteur_players:
            return VictoryResult(checked_timing=timing, details={"saboteur_present": False})

        for condition in self.config.victory.saboteur_win_conditions:
            if self._saboteur_condition_met(state, condition):
                return VictoryResult(
                    is_win=True,
                    winner_type="saboteur",
                    winner_player=saboteur_players[0].id if len(saboteur_players) == 1 else None,
                    winning_condition=condition.type,
                    checked_timing=timing,
                    tie_info={"saboteur_players": [player.id for player in saboteur_players]}
                    if len(saboteur_players) > 1
                    else None,
                    details={"condition": condition.model_dump()},
                )
        return VictoryResult(checked_timing=timing, details={"saboteur_present": True, "winner": None})

    def _saboteur_condition_met(self, state: GameState, condition: SaboteurWinConditionConfig) -> bool:
        faction_population = sum(faction.population for faction in state.factions.values())
        live_total_population = faction_population + state.neutral_population
        destroyed_population = self.config.population.total_population - live_total_population
        eliminated_factions = sum(1 for faction in state.factions.values() if faction.population == 0)

        if condition.type == "total_population_zero":
            return live_total_population == 0
        if condition.type == "all_factions_zero":
            return all(faction.population == 0 for faction in state.factions.values())
        if condition.type == "destroyed_population_at_least":
            return condition.threshold is not None and destroyed_population >= condition.threshold
        if condition.type == "factions_eliminated_at_least":
            return condition.threshold is not None and eliminated_factions >= condition.threshold
        return False

    def _check_faction_game_end(self, state: GameState, timing: str) -> VictoryResult:
        populations = {faction_id: faction.population for faction_id, faction in state.factions.items()}
        highest_population = max(populations.values())
        tied_factions = [faction_id for faction_id, population in populations.items() if population == highest_population]

        if len(tied_factions) > 1:
            if self.config.victory.tie_breakers == "no_winner":
                return VictoryResult(
                    is_win=True,
                    winner_type="draw",
                    winning_condition="highest_population_at_game_end",
                    checked_timing=timing,
                    tie_info={"tied_factions": tied_factions, "mode": "no_winner"},
                    details={"final_populations": populations},
                )
            if self.config.victory.tie_breakers == "configured_order":
                configured_order = [faction.id for faction in self.config.factions]
                winner_faction = next(faction_id for faction_id in configured_order if faction_id in tied_factions)
                return self._faction_result(state, timing, winner_faction, {"tied_factions": tied_factions})
            return VictoryResult(
                is_win=True,
                winner_type="faction",
                winner_faction=None,
                winner_player=None,
                winning_condition="highest_population_at_game_end",
                checked_timing=timing,
                tie_info={
                    "tied_factions": tied_factions,
                    "mode": "shared_win",
                    "winner_players": self._players_for_factions(state, tied_factions),
                },
                details={"final_populations": populations},
            )

        return self._faction_result(state, timing, tied_factions[0], None)

    def _faction_result(
        self,
        state: GameState,
        timing: str,
        winner_faction: str,
        tie_info: dict | None,
    ) -> VictoryResult:
        winner_players = self._players_for_factions(state, [winner_faction])
        return VictoryResult(
            is_win=True,
            winner_type="faction",
            winner_player=winner_players[0] if len(winner_players) == 1 else None,
            winner_faction=winner_faction,
            winning_condition="highest_population_at_game_end",
            checked_timing=timing,
            tie_info=tie_info if tie_info is not None else ({"winner_players": winner_players} if len(winner_players) > 1 else None),
            details={"final_populations": {key: faction.population for key, faction in state.factions.items()}},
        )

    def _players_for_factions(self, state: GameState, faction_ids: list[str]) -> list[str]:
        return [
            player.id
            for player in state.players.values()
            if not player.is_saboteur and player.secret_faction_id in faction_ids
        ]

