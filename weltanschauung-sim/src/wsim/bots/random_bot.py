from __future__ import annotations

from typing import Any

from wsim.bots.base import BaseBot, BotContext, BotDecision
from wsim.core.state import GameRng


class RandomBot(BaseBot):
    def choose_draft_pick(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_draft_pick", options, rng)

    def choose_draft_pass(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_draft_pass", options, rng)

    def choose_journalist_action(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_journalist_action", options, rng)

    def choose_media_mogul_card(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_media_mogul_card", options, rng)

    def choose_cards_to_commit(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_cards_to_commit", options, rng)

    def choose_action_type(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_action_type", options, rng)

    def choose_acting_faction(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_acting_faction", options, rng)

    def choose_target_faction(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_target_faction", options, rng)

    def choose_source_use(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_source_use", options, rng)

    def choose_research_order_priority(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_research_order_priority", options, rng)

    def _choose(self, method_name: str, options: list[Any], rng: GameRng) -> BotDecision:
        if not options:
            return BotDecision(
                choice=None,
                reason=f"RandomBot had no legal options for {method_name}; returning None.",
                considered_options=[],
            )
        choice = rng.choose(options)
        return BotDecision(
            choice=choice,
            reason=f"RandomBot selected uniformly from {len(options)} legal option(s) for {method_name}.",
            considered_options=list(options),
        )

