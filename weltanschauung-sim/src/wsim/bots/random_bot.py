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

    def how_many_sources_to_bid(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("how_many_sources_to_bid", options, rng)

    def which_player_to_vote_for(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("which_player_to_vote_for", options, rng)

    def choose_card_to_contribute_to_draft(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_card_to_contribute_to_draft", options, rng)

    def choose_card_to_take_from_three(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_card_to_take_from_three", options, rng)

    def choose_future_set_or_remove_propaganda(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_future_set_or_remove_propaganda", options, rng)

    def choose_card_to_put_on_top_of_draw_deck(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_card_to_put_on_top_of_draw_deck", options, rng)

    def choose_propaganda_to_remove(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_propaganda_to_remove", options, rng)

    def choose_pool_card_to_discard_as_cost(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_pool_card_to_discard_as_cost", options, rng)

    def choose_one_of_two_as_new_propaganda(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_one_of_two_as_new_propaganda", options, rng)

    def choose_number_of_cards_for_urn(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_number_of_cards_for_urn", options, rng)

    def choose_cards_for_urn(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_cards_for_urn", options, rng)

    def choose_completed_research_assignment_to_score(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_completed_research_assignment_to_score", options, rng)

    def choose_whether_to_discard_research_assignment(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_whether_to_discard_research_assignment", options, rng)

    def choose_research_assignment_to_discard(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose("choose_research_assignment_to_discard", options, rng)

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
