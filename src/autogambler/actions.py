from __future__ import annotations

from dataclasses import dataclass

from autogambler.config import ConfigBundle, ConfigError
from autogambler.state import BotView, PlannedAction


@dataclass(frozen=True)
class LegalAction:
    action_type_id: str
    target_faction_id: str | None = None
    card_id: str | None = None

    def to_planned_action(self, player_id: str) -> PlannedAction:
        return PlannedAction(
            player_id=player_id,
            action_type_id=self.action_type_id,
            target_faction_id=self.target_faction_id,
            card_id=self.card_id,
        )


def legal_actions_for_view(config: ConfigBundle, view: BotView) -> list[LegalAction]:
    actions: list[LegalAction] = []
    card_by_action = {card.action_type_id: card.id for card in config.cards.cards if card.id in view.own_hand}
    faction_ids = list(view.public_faction_population)
    for action_type in config.game.action_types:
        available_card_id = card_by_action.get(action_type.id)
        if view.own_hand and available_card_id is None:
            continue
        targets = faction_ids if action_type.requires_target_faction else [None]
        for target in targets:
            actions.append(
                LegalAction(
                    action_type_id=action_type.id,
                    target_faction_id=target,
                    card_id=available_card_id,
                )
            )
    if not actions:
        raise ConfigError(f"No legal actions for player {view.player_id}; check cards and action_types.")
    return actions

