from __future__ import annotations

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
        return BotContext(
            player_id=player_id,
            public_view=state.to_public_view(player_id),
            rules=self.rules,
            cards_by_id=self.cards_by_id,
        )

    def draft_pick_options(self, context: BotContext) -> list[str]:
        return list(context.public_view["players"][context.player_id]["hand"])

    def draft_pass_options(self, context: BotContext) -> list[bool]:
        return [True, False]

    def journalist_action_options(self, context: BotContext) -> list[str]:
        return ["observe", "publish_stub_warning"]

    def media_mogul_card_options(self, context: BotContext) -> list[str]:
        return list(context.public_view["players"][context.player_id]["hand"])

    def cards_to_commit_options(self, context: BotContext) -> list[list[str]]:
        hand = list(context.public_view["players"][context.player_id]["hand"])
        options: list[list[str]] = [[]]
        options.extend([[card_id] for card_id in hand])
        return options

    def action_type_options(self, context: BotContext) -> list[str]:
        hand = context.public_view["players"][context.player_id]["hand"]
        card_types = {
            self.cards_by_id[card_id].type
            for card_id in hand
            if card_id in self.cards_by_id and self.cards_by_id[card_id].type in {"action", "propaganda", "hybrid"}
        }
        return sorted(card_types) or ["pass"]

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
        }

