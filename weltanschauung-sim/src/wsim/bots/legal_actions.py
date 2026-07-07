from __future__ import annotations

from itertools import combinations
from typing import Any

from wsim.core.models import CardConfig, GameConfig
from wsim.core.state import GameState
from wsim.bots.base import BotContext


class LegalActionProvider:
    def __init__(self, rules: GameConfig, cards: list[CardConfig]) -> None:
        self.rules = rules
        self.cards = cards
        self.cards_by_id = {card.id: card for card in cards}

    def build_context(self, state: GameState, player_id: str) -> BotContext:
        cards_by_id = dict(self.cards_by_id)
        for instance_id, instance in state.deck.card_instances.items():
            if instance.card_id in self.cards_by_id:
                cards_by_id[instance_id] = self.cards_by_id[instance.card_id]
        return BotContext(
            player_id=player_id,
            public_view=state.to_public_view(player_id),
            rules=self.rules,
            cards_by_id=cards_by_id,
        )

    def draft_pick_options(self, context: BotContext) -> list[str]:
        return list(context.public_view["players"][context.player_id]["hand"])

    def draft_pass_options(self, context: BotContext) -> list[bool]:
        return [True, False]

    def journalist_action_options(self, context: BotContext) -> list[Any]:
        options: list[dict[str, Any]] = []
        for position, card_id in enumerate(context.public_view["propaganda_track"]["slots"], start=1):
            if card_id is None:
                continue
            for action in context.rules.propaganda.journalist_options:
                options.append({"action": action, "card_id": card_id, "slot": position})
        return options or [{"action": "pass"}]

    def media_mogul_card_options(self, context: BotContext) -> list[str]:
        allowed_types = set(context.rules.propaganda.media_mogul_card_types)
        hand = context.public_view["players"][context.player_id]["hand"]
        return [
            card_id
            for card_id in hand
            if card_id in self.cards_by_id and self.cards_by_id[card_id].type in allowed_types
        ]

    def cards_to_commit_options(self, context: BotContext) -> list[list[str]]:
        hand = list(context.public_view["players"][context.player_id]["hand"])
        options: list[list[str]] = [[]]
        options.extend([[card_id] for card_id in hand])
        return options

    def action_type_options(self, context: BotContext) -> list[str]:
        return ["support", "attack"]

    def acting_faction_options(self, context: BotContext) -> list[str]:
        player_view = context.public_view["players"][context.player_id]
        options = [player_view["public_faction_id"]]
        secret_faction_id = player_view.get("secret_faction_id")
        if secret_faction_id and secret_faction_id not in options:
            options.append(secret_faction_id)
        return options

    def target_faction_options(self, context: BotContext) -> list[str]:
        return sorted(context.public_view["factions"].keys())

    def source_use_options(self, context: BotContext) -> list[str | None]:
        sources = list(context.public_view["players"][context.player_id]["sources"])
        return [None, *sources]

    def research_order_priority_options(self, context: BotContext) -> list[str]:
        return list(context.public_view["players"][context.player_id]["hidden_research_orders"])

    def source_bid_options(self, context: BotContext) -> list[int]:
        player = context.public_view["players"][context.player_id]
        source_count = len(player.get("sources", []))
        return list(range(source_count + 1))

    def player_vote_options(self, context: BotContext) -> list[str]:
        return list(context.public_view["players"].keys())

    def draft_contribution_options(self, context: BotContext) -> list[str]:
        return list(context.public_view["players"][context.player_id]["hand"])

    def draft_take_from_three_options(self, context: BotContext, visible_cards: list[str] | None = None) -> list[str]:
        if visible_cards is not None:
            return list(visible_cards)
        return list(context.public_view["players"][context.player_id]["hand"])[:3]

    def journalist_v03_action_options(self, context: BotContext, has_propaganda: bool | None = None) -> list[str]:
        options = ["future_set"]
        track_has_propaganda = has_propaganda if has_propaganda is not None else any(context.public_view["propaganda_track"]["slots"])
        if track_has_propaganda:
            options.append("remove_propaganda")
        return options

    def top_of_deck_card_options(self, _context: BotContext, pool: list[str]) -> list[str]:
        return list(pool)

    def propaganda_to_remove_options(self, context: BotContext) -> list[str]:
        return [card_id for card_id in context.public_view["propaganda_track"]["slots"] if card_id is not None]

    def media_mogul_v03_card_options(self, _context: BotContext, pool: list[str]) -> list[str]:
        return list(pool[:2])

    def urn_count_options(self, context: BotContext) -> list[int]:
        hand_size = len(context.public_view["players"][context.player_id]["hand"])
        if hand_size <= 0:
            return [0]
        return list(range(1, min(3, hand_size) + 1))

    def urn_card_options(self, context: BotContext, count: int | None = None) -> list[list[str]]:
        hand = list(context.public_view["players"][context.player_id]["hand"])
        if not hand:
            return [[]]
        counts = [count] if count is not None else range(1, min(3, len(hand)) + 1)
        return [list(combo) for current_count in counts for combo in combinations(hand, current_count)]

    def research_assignment_discard_options(self, context: BotContext) -> list[str]:
        return list(context.public_view["players"][context.player_id]["hidden_research_orders"])

    def all_options(self, context: BotContext) -> dict[str, list[Any]]:
        return {
            "choose_draft_pick": self.draft_pick_options(context),
            "choose_draft_pass": self.draft_pass_options(context),
            "choose_journalist_action": self.journalist_action_options(context),
            "choose_media_mogul_card": self.media_mogul_card_options(context),
            "choose_cards_to_commit": self.cards_to_commit_options(context),
            "choose_action_type": self.action_type_options(context),
            "choose_acting_faction": self.acting_faction_options(context),
            "choose_target_faction": self.target_faction_options(context),
            "choose_source_use": self.source_use_options(context),
            "choose_research_order_priority": self.research_order_priority_options(context),
            "how_many_sources_to_bid": self.source_bid_options(context),
            "which_player_to_vote_for": self.player_vote_options(context),
            "choose_card_to_contribute_to_draft": self.draft_contribution_options(context),
            "choose_card_to_take_from_three": self.draft_take_from_three_options(context),
            "choose_future_set_or_remove_propaganda": self.journalist_v03_action_options(context),
            "choose_card_to_put_on_top_of_draw_deck": self.draft_contribution_options(context) or [None],
            "choose_propaganda_to_remove": self.propaganda_to_remove_options(context) or [None],
            "choose_pool_card_to_discard_as_cost": self.draft_contribution_options(context) or [None],
            "choose_one_of_two_as_new_propaganda": self.media_mogul_card_options(context) or [None],
            "choose_number_of_cards_for_urn": self.urn_count_options(context),
            "choose_cards_for_urn": self.urn_card_options(context),
            "choose_completed_research_assignment_to_score": self.research_order_priority_options(context),
            "choose_whether_to_discard_research_assignment": [True, False],
            "choose_research_assignment_to_discard": self.research_assignment_discard_options(context) or [None],
        }
