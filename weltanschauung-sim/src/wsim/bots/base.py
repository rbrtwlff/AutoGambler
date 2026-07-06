from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from wsim.core.models import BotConfig, CardConfig, GameConfig
from wsim.core.state import GameRng


class BotDecision(BaseModel):
    choice: Any
    reason: str
    score: float | None = None
    considered_options: list[Any] | None = None


class BotContext(BaseModel):
    player_id: str
    public_view: dict[str, Any]
    rules: GameConfig
    cards_by_id: dict[str, CardConfig] = Field(default_factory=dict)


class BaseBot(ABC):
    def __init__(self, config: BotConfig) -> None:
        self.config = config

    @abstractmethod
    def choose_draft_pick(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_draft_pass(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_journalist_action(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_media_mogul_card(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_cards_to_commit(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_action_type(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_acting_faction(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_target_faction(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_source_use(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

    @abstractmethod
    def choose_research_order_priority(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        raise NotImplementedError

