from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from rich.progress import track

from wsim.core.models import BotConfig, CardConfig, GameConfig
from wsim.engine.game_engine import GameEngine
from wsim.engine.invariants import InvariantError, export_debug_snapshot


class SmokeTestResult(BaseModel):
    games: int
    master_seed: int
    debug_dir: Path


class SmokeTestError(RuntimeError):
    def __init__(self, message: str, *, game_index: int, seed: int, debug_dir: Path) -> None:
        self.game_index = game_index
        self.seed = seed
        self.debug_dir = debug_dir
        super().__init__(message)


def run_smoke_test(
    *,
    rules: GameConfig,
    cards: list[CardConfig],
    bots: list[BotConfig],
    games: int,
    master_seed: int,
    debug_dir: Path,
    show_progress: bool = True,
) -> SmokeTestResult:
    iterable = range(games)
    if show_progress:
        iterable = track(iterable, total=games, description="Smoke testing games")

    for game_index in iterable:
        seed = master_seed + game_index
        engine: GameEngine | None = None
        try:
            engine = GameEngine(rules, cards, seed=seed, bots=bots)
            engine.run_game()
        except (InvariantError, Exception) as exc:
            if engine is not None and rules.quality.debug_export_on_error:
                export_debug_snapshot(
                    config=rules,
                    state=engine.state,
                    output_dir=debug_dir,
                    game_index=game_index,
                    error=exc,
                )
            raise SmokeTestError(
                f"Smoke test failed for game_index={game_index}, seed={seed}: {exc}",
                game_index=game_index,
                seed=seed,
                debug_dir=debug_dir,
            ) from exc
    return SmokeTestResult(games=games, master_seed=master_seed, debug_dir=debug_dir)
