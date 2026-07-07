from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from wsim.config import ConfigError
from wsim.core.events import EventBus, EventType
from wsim.core.models import CardConfig, GameConfig
from wsim.core.state import GameState, RevealedAction

TriggerName = Literal[
    "on_reveal",
    "before_action_resolution",
    "after_action_resolution",
    "before_population_change",
    "after_population_change",
    "on_round_start",
    "on_round_end",
    "on_victory_check",
    "when_in_propaganda_before_action_resolution",
]

VALID_TRIGGERS = {
    "on_reveal",
    "before_action_resolution",
    "after_action_resolution",
    "before_population_change",
    "after_population_change",
    "on_round_start",
    "on_round_end",
    "on_victory_check",
    "when_in_propaganda_before_action_resolution",
}
VALID_CONDITIONS = {
    "action_type_is",
    "acting_faction_is",
    "target_faction_is",
    "card_faction_is",
    "this_card_slot_is",
    "propaganda_contains_faction_count",
    "propaganda_contains_tag_count",
    "faction_population_below",
    "faction_population_above",
    "neutral_population_below",
    "round_number_at_least",
    "player_has_role",
    "player_secret_faction_is",
}
VALID_EFFECTS = {
    "add_strength",
    "subtract_strength",
    "add_population",
    "remove_population",
    "draw_card",
    "discard_card",
    "remove_propaganda",
    "prevent_population_loss",
    "advance_counter",
}


class EffectResult(BaseModel):
    triggered: bool
    card_id: str | None = None
    trigger: str
    effect_type: str
    amount: int = 0
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass
class EffectContext:
    config: GameConfig
    cards_by_id: dict[str, CardConfig]
    state: GameState
    event_bus: EventBus
    trigger: str
    source_card: CardConfig | None = None
    revealed_action: RevealedAction | None = None
    acting_player_id: str | None = None
    target_faction_id: str | None = None
    population_delta: int = 0
    prevented_population_loss: int = 0
    counters: dict[str, int] = field(default_factory=dict)


class ConditionEvaluator:
    def __init__(self, cards_by_id: dict[str, CardConfig]) -> None:
        self.cards_by_id = cards_by_id

    def conditions_met(self, conditions: list[dict[str, Any]], context: EffectContext) -> bool:
        return all(self.condition_met(condition, context) for condition in conditions)

    def condition_met(self, condition: dict[str, Any], context: EffectContext) -> bool:
        condition_type = condition.get("type") or condition.get("condition")
        if condition_type not in VALID_CONDITIONS:
            raise ConfigError(f"Unknown condition type: {condition_type}")

        if condition_type == "action_type_is":
            return context.revealed_action is not None and context.revealed_action.action_type == condition.get("value")
        if condition_type == "acting_faction_is":
            return context.revealed_action is not None and context.revealed_action.acting_faction_id == condition.get("value")
        if condition_type == "target_faction_is":
            return context.target_faction_id == condition.get("value") or (
                context.revealed_action is not None and context.revealed_action.target_faction_id == condition.get("value")
            )
        if condition_type == "card_faction_is":
            return context.source_card is not None and _normalize_faction(context.source_card.faction) == condition.get("value")
        if condition_type == "this_card_slot_is":
            return self._this_card_slot(context) == int(condition.get("position", condition.get("value", -1)))
        if condition_type == "propaganda_contains_faction_count":
            faction_id = self._resolve_faction_reference(condition.get("faction", "this_card"), context)
            return self._propaganda_faction_count(context, faction_id) >= int(condition.get("min_count", condition.get("count", 1)))
        if condition_type == "propaganda_contains_tag_count":
            tag = condition.get("tag")
            return self._propaganda_tag_count(context, tag) >= int(condition.get("min_count", condition.get("count", 1)))
        if condition_type == "faction_population_below":
            faction_id = self._resolve_faction_reference(condition.get("faction", "target"), context)
            return context.state.factions[faction_id].population < int(condition.get("value", condition.get("threshold", 0)))
        if condition_type == "faction_population_above":
            faction_id = self._resolve_faction_reference(condition.get("faction", "target"), context)
            return context.state.factions[faction_id].population > int(condition.get("value", condition.get("threshold", 0)))
        if condition_type == "neutral_population_below":
            return context.state.neutral_population < int(condition.get("value", condition.get("threshold", 0)))
        if condition_type == "round_number_at_least":
            return context.state.round.round_number >= int(condition.get("value", condition.get("round", 1)))
        if condition_type == "player_has_role":
            player_id = context.acting_player_id or (context.revealed_action.player_id if context.revealed_action else None)
            return player_id is not None and condition.get("role") in context.state.players[player_id].roles
        if condition_type == "player_secret_faction_is":
            player_id = context.acting_player_id or (context.revealed_action.player_id if context.revealed_action else None)
            return player_id is not None and context.state.players[player_id].secret_faction_id == condition.get("value")
        return False

    def _this_card_slot(self, context: EffectContext) -> int | None:
        if context.source_card is None:
            return None
        slots = context.state.propaganda_track.get_slots()
        if context.source_card.id not in slots:
            for index, card_id in enumerate(slots):
                if card_id is not None and card_id in self.cards_by_id and self.cards_by_id[card_id].id == context.source_card.id:
                    return index + 1
            return None
        return slots.index(context.source_card.id) + 1

    def _propaganda_faction_count(self, context: EffectContext, faction_id: str | None) -> int:
        return sum(
            1
            for card_id in context.state.propaganda_track.get_slots()
            if card_id is not None and _normalize_faction(self.cards_by_id[card_id].faction) == faction_id
        )

    def _propaganda_tag_count(self, context: EffectContext, tag: str | None) -> int:
        return sum(
            1
            for card_id in context.state.propaganda_track.get_slots()
            if card_id is not None and tag in self.cards_by_id[card_id].tags
        )

    def _resolve_faction_reference(self, value: Any, context: EffectContext) -> str:
        if value in (None, "target"):
            if context.target_faction_id is not None:
                return context.target_faction_id
            if context.revealed_action is not None:
                return context.revealed_action.target_faction_id
        if value == "acting" and context.revealed_action is not None:
            return context.revealed_action.acting_faction_id
        if value == "this_card" and context.source_card is not None:
            faction_id = _normalize_faction(context.source_card.faction)
            if faction_id is not None:
                return faction_id
        if isinstance(value, str):
            return value
        raise ConfigError(f"Could not resolve faction reference: {value}")


class EffectExecutor:
    def __init__(self, cards_by_id: dict[str, CardConfig]) -> None:
        self.cards_by_id = cards_by_id

    def execute(self, effect: dict[str, Any], context: EffectContext) -> EffectResult:
        effect_type = effect.get("type") or effect.get("effect")
        if effect_type not in VALID_EFFECTS:
            raise ConfigError(f"Unknown effect type: {effect_type}")

        if effect_type == "add_strength":
            amount = _effect_amount(effect)
            if context.revealed_action is not None:
                context.revealed_action.strength += amount
            return self._result(effect_type, amount, context)
        if effect_type == "subtract_strength":
            amount = _effect_amount(effect)
            if context.revealed_action is not None:
                context.revealed_action.strength = max(0, context.revealed_action.strength - amount)
            return self._result(effect_type, -amount, context)
        if effect_type == "add_population":
            amount = _effect_amount(effect)
            faction_id = self._effect_target_faction(effect, context)
            available = min(amount, context.state.neutral_population)
            context.state.factions[faction_id].population += available
            context.state.neutral_population -= available
            return self._result(effect_type, available, context, {"target_faction_id": faction_id})
        if effect_type == "remove_population":
            amount = _effect_amount(effect)
            faction_id = self._effect_target_faction(effect, context)
            removed = min(amount, context.state.factions[faction_id].population)
            context.state.factions[faction_id].population -= removed
            return self._result(effect_type, -removed, context, {"target_faction_id": faction_id})
        if effect_type == "draw_card":
            return self._draw_card(effect, context)
        if effect_type == "discard_card":
            return self._discard_card(effect, context)
        if effect_type == "remove_propaganda":
            return self._remove_propaganda(effect, context)
        if effect_type == "prevent_population_loss":
            amount = _effect_amount(effect)
            context.prevented_population_loss += amount
            context.counters["prevent_population_loss"] = context.counters.get("prevent_population_loss", 0) + amount
            return self._result(effect_type, amount, context)
        if effect_type == "advance_counter":
            counter = str(effect.get("counter", "default"))
            amount = _effect_amount(effect, default=1)
            context.counters[counter] = context.counters.get(counter, 0) + amount
            return self._result(effect_type, amount, context, {"counter": counter, "value": context.counters[counter]})
        raise ConfigError(f"Unknown effect type: {effect_type}")

    def _draw_card(self, effect: dict[str, Any], context: EffectContext) -> EffectResult:
        amount = _effect_amount(effect, default=1)
        player_id = self._effect_player(effect, context)
        drawn: list[str] = []
        drawn = context.state.deck.draw_cards(
            amount,
            config=context.config.deck,
            rng=context.state.rng,
            event_bus=context.event_bus,
            reason="effect_draw_card",
            player_id=player_id,
            destination="hand",
            game_id=context.config.game_id,
            round_number=context.state.round.round_number,
            phase=context.state.round.phase,
        )
        context.state.players[player_id].hand.extend(drawn)
        return self._result("draw_card", len(drawn), context, {"player_id": player_id, "drawn_card_ids": drawn})

    def _discard_card(self, effect: dict[str, Any], context: EffectContext) -> EffectResult:
        player_id = self._effect_player(effect, context)
        card_id = effect.get("card_id")
        discarded = None
        if card_id is not None and card_id in context.state.players[player_id].hand:
            context.state.players[player_id].hand.remove(card_id)
            context.state.deck.discard_card(card_id, event_bus=context.event_bus, reason="effect_discard_card", player_id=player_id)
            discarded = card_id
        return self._result("discard_card", 1 if discarded else 0, context, {"player_id": player_id, "card_id": discarded})

    def _remove_propaganda(self, effect: dict[str, Any], context: EffectContext) -> EffectResult:
        card_id = effect.get("card_id")
        if card_id in (None, "this_card") and context.source_card is not None:
            card_id = context.source_card.id
        if isinstance(card_id, str) and card_id not in context.state.propaganda_track.get_slots():
            card_id = _find_propaganda_instance(context, card_id)
        removed = context.state.propaganda_track.remove_card(card_id) if isinstance(card_id, str) else None
        if removed is not None:
            context.state.deck.discard_card(removed, event_bus=context.event_bus, reason="effect_remove_propaganda")
        return self._result("remove_propaganda", 1 if removed else 0, context, {"card_id": removed})

    def _effect_target_faction(self, effect: dict[str, Any], context: EffectContext) -> str:
        value = effect.get("target", effect.get("faction", "target"))
        return ConditionEvaluator(self.cards_by_id)._resolve_faction_reference(value, context)

    def _effect_player(self, effect: dict[str, Any], context: EffectContext) -> str:
        value = effect.get("player", "acting")
        if value == "acting":
            if context.acting_player_id is not None:
                return context.acting_player_id
            if context.revealed_action is not None:
                return context.revealed_action.player_id
        if isinstance(value, str) and value in context.state.players:
            return value
        raise ConfigError(f"Could not resolve player reference: {value}")

    def _result(
        self,
        effect_type: str,
        amount: int,
        context: EffectContext,
        payload: dict[str, Any] | None = None,
    ) -> EffectResult:
        return EffectResult(
            triggered=True,
            card_id=context.source_card.id if context.source_card else None,
            trigger=context.trigger,
            effect_type=effect_type,
            amount=amount,
            payload=payload or {},
        )


class EffectEngine:
    def __init__(self, config: GameConfig, cards: list[CardConfig], event_bus: EventBus) -> None:
        self.config = config
        self.cards_by_id = {card.id: card for card in cards}
        self.event_bus = event_bus
        self.condition_evaluator = ConditionEvaluator(self.cards_by_id)
        self.executor = EffectExecutor(self.cards_by_id)
        self.counters: dict[str, int] = {}

    def trigger(
        self,
        trigger: str,
        *,
        state: GameState,
        revealed_action: RevealedAction | None = None,
        source_cards: list[CardConfig] | None = None,
        target_faction_id: str | None = None,
        population_delta: int = 0,
    ) -> list[EffectResult]:
        if trigger not in VALID_TRIGGERS:
            raise ConfigError(f"Unknown trigger: {trigger}")

        cards = source_cards if source_cards is not None else self._cards_for_trigger(trigger, revealed_action, state)
        results: list[EffectResult] = []
        for card in cards:
            for effect in self._effects_for_card(card, trigger):
                conditions = effect.get("conditions", [])
                context = EffectContext(
                    config=self.config,
                    cards_by_id=self.cards_by_id,
                    state=state,
                    event_bus=self.event_bus,
                    trigger=trigger,
                    source_card=card,
                    revealed_action=revealed_action,
                    acting_player_id=revealed_action.player_id if revealed_action else None,
                    target_faction_id=target_faction_id,
                    population_delta=population_delta,
                    counters=self.counters,
                )
                if not self.condition_evaluator.conditions_met(conditions, context):
                    continue
                result = self.executor.execute(effect, context)
                results.append(result)
                self._emit_effect_event(effect, result, context)
        return results

    def _cards_for_trigger(self, trigger: str, revealed_action: RevealedAction | None, state: GameState) -> list[CardConfig]:
        if trigger == "when_in_propaganda_before_action_resolution":
            return [self.cards_by_id[card_id] for card_id in state.propaganda_track.get_slots() if card_id is not None]
        if revealed_action is not None:
            return [self.cards_by_id[card_id] for card_id in revealed_action.committed_card_ids if card_id in self.cards_by_id]
        return []

    def _effects_for_card(self, card: CardConfig, trigger: str) -> list[dict[str, Any]]:
        effects = card.propaganda_effects if trigger == "when_in_propaganda_before_action_resolution" else card.action_effects
        return [effect for effect in effects if effect.get("trigger") == trigger]

    def _emit_effect_event(self, effect: dict[str, Any], result: EffectResult, context: EffectContext) -> None:
        self.event_bus.emit(
            EventType.EFFECT_TRIGGERED,
            {
                "card_id": result.card_id,
                "trigger": result.trigger,
                "effect_type": result.effect_type,
                "amount": result.amount,
                "effect": effect,
                "result": result.model_dump(),
                "player_id": context.acting_player_id,
                "target_faction_id": context.target_faction_id,
                "round": context.state.round.round_number,
            },
        )


def validate_card_effects(cards: list[CardConfig]) -> None:
    for card in cards:
        for effect in [*card.action_effects, *card.propaganda_effects]:
            trigger = effect.get("trigger")
            effect_type = effect.get("type") or effect.get("effect")
            if trigger not in VALID_TRIGGERS:
                raise ConfigError(f"Card {card.id} has unknown effect trigger: {trigger}")
            if effect_type not in VALID_EFFECTS:
                raise ConfigError(f"Card {card.id} has unknown effect type: {effect_type}")
            for condition in effect.get("conditions", []):
                condition_type = condition.get("type") or condition.get("condition")
                if condition_type not in VALID_CONDITIONS:
                    raise ConfigError(f"Card {card.id} has unknown condition type: {condition_type}")


def _effect_amount(effect: dict[str, Any], default: int = 0) -> int:
    return int(effect.get("amount", effect.get("value", default)))


def _normalize_faction(faction: str | None) -> str | None:
    if faction in (None, "", "neutral"):
        return None
    return faction


def _find_propaganda_instance(context: EffectContext, logical_card_id: str) -> str | None:
    for instance_id in context.state.propaganda_track.get_slots():
        if instance_id is None:
            continue
        if instance_id in context.state.deck.card_instances:
            if context.state.deck.card_instances[instance_id].card_id == logical_card_id:
                return instance_id
    return None
