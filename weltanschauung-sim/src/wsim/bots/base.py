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

    def choose_source_bid(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        choice = rng.choose(options) if options else 0
        return BotDecision(choice=choice, reason=f"{self.__class__.__name__} selected a legal source bid.", considered_options=list(options))

    def choose_player_vote(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        choice = rng.choose(options) if options else None
        return BotDecision(choice=choice, reason=f"{self.__class__.__name__} selected a legal player vote.", considered_options=list(options))

    def choose_urn_cards(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        choice = rng.choose(options) if options else []
        return BotDecision(choice=choice, reason=f"{self.__class__.__name__} selected legal urn card(s).", considered_options=list(options))

    def how_many_sources_to_bid(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_source_bid(context, options, rng)

    def which_player_to_vote_for(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_player_vote(context, options, rng)

    def choose_card_to_contribute_to_draft(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_draft_pick(context, options, rng)

    def choose_card_to_take_from_three(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_draft_pick(context, options, rng)

    def choose_future_set_or_remove_propaganda(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_journalist_action(context, options, rng)

    def choose_card_to_put_on_top_of_draw_deck(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_draft_pick(context, options, rng)

    def choose_propaganda_to_remove(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_journalist_action(context, options, rng)

    def choose_pool_card_to_discard_as_cost(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_draft_pick(context, options, rng)

    def choose_one_of_two_as_new_propaganda(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_media_mogul_card(context, options, rng)

    def choose_number_of_cards_for_urn(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        choice = rng.choose(options) if options else 0
        return BotDecision(choice=choice, reason=f"{self.__class__.__name__} selected a legal urn card count.", considered_options=list(options))

    def choose_cards_for_urn(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_urn_cards(context, options, rng)

    def choose_completed_research_assignment_to_score(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_research_order_priority(context, options, rng)

    def choose_whether_to_discard_research_assignment(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        choice = rng.choose(options) if options else False
        return BotDecision(choice=choice, reason=f"{self.__class__.__name__} selected whether to discard a research assignment.", considered_options=list(options))

    def choose_research_assignment_to_discard(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self.choose_research_order_priority(context, options, rng)
