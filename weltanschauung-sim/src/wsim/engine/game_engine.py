from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from wsim.bots import BotContext, BotFactory, LegalActionProvider
from wsim.core.events import EventBus, EventType
from wsim.core.models import BotConfig, CardConfig, GameConfig
from wsim.core.state import GameState, PlannedAction, ResolvedAction, RevealedAction
from wsim.engine.effects import EffectEngine
from wsim.engine.setup import create_initial_state
from wsim.engine.victory import VictoryChecker, VictoryResult


class GameResult(BaseModel):
    game_id: str
    seed: int
    rounds_played: int
    ended_by: Literal["victory", "max_rounds"]
    winner_type: Literal["faction", "saboteur", "draw", "none"] = "none"
    winner_player: str | None = None
    winner_faction: str | None = None
    winning_condition: str | None = None
    tie_info: dict | None = None
    final_populations: dict[str, int]
    event_count: int


@dataclass
class PhaseContext:
    config: GameConfig
    cards: list[CardConfig]
    state: GameState
    event_bus: EventBus
    phase_name: str


class GameEngine:
    def __init__(self, config: GameConfig, cards: list[CardConfig], seed: int, bots: list[BotConfig] | None = None) -> None:
        self.config = config
        self.cards = cards
        self.seed = seed
        self.state = create_initial_state(config, cards, seed=seed)
        self.event_bus = EventBus(
            run_id=f"{config.game_id}-{seed}",
            game_id=config.game_id,
            round_number=self.state.round.round_number,
            phase=self.state.round.phase,
            event_log=self.state.event_log,
        )
        self.ended_by: Literal["victory", "max_rounds"] | None = None
        self.winner_type: Literal["faction", "saboteur", "draw", "none"] = "none"
        self.winner_player: str | None = None
        self.winner_faction: str | None = None
        self.winning_condition: str | None = None
        self.tie_info: dict | None = None
        self.legal_actions = LegalActionProvider(config, cards)
        bot_configs = bots or config.bots or [
            BotConfig(id=f"{player.id}_random", player_id=player.id, type="random")
            for player in config.players
        ]
        self.bots = BotFactory().create_all(bot_configs)
        self.cards_by_id = {card.id: card for card in cards}
        self.victory_checker = VictoryChecker(config)
        self.effect_engine = EffectEngine(config, cards, self.event_bus)

    def run_game(self) -> GameResult:
        while self.ended_by is None and self.state.round.round_number < self.config.round_flow.max_rounds:
            self.run_round()

        if self.ended_by is None:
            self._check_victory("game_end")

        if self.ended_by is None:
            self.ended_by = "max_rounds"
            self.event_bus.set_context(round_number=self.state.round.round_number, phase=self.state.round.phase)
            self.event_bus.emit(
                EventType.GAME_ENDED,
                {
                    "ended_by": self.ended_by,
                    "max_rounds": self.config.round_flow.max_rounds,
                    "winner_type": self.winner_type,
                    "winner_player": self.winner_player,
                    "winner_faction": self.winner_faction,
                    "winning_condition": self.winning_condition,
                    "tie_info": self.tie_info,
                },
            )

        return self._build_result()

    def run_round(self) -> None:
        if self.ended_by is not None:
            return

        self.state.round.round_number += 1
        self.state.round.phase = "start_round"
        self.event_bus.set_context(round_number=self.state.round.round_number, phase=self.state.round.phase)
        self.event_bus.emit(
            EventType.ROUND_STARTED,
            {
                "round": self.state.round.round_number,
                "start_player_id": self.state.round.start_player_id,
            },
        )

        for phase_name in self.config.round_flow.phases:
            if self.ended_by is not None:
                break
            self.run_phase(phase_name)
        if (
            self.ended_by is None
            and "end_of_round" in self.config.victory.check_timing
            and "victory_check" not in self.config.round_flow.phases
        ):
            self._check_victory("end_of_round")

    def run_phase(self, phase_name: str) -> None:
        self.state.round.phase = phase_name
        self.event_bus.set_context(round_number=self.state.round.round_number, phase=phase_name)
        self.event_bus.emit(EventType.PHASE_STARTED, {"phase": phase_name})

        context = PhaseContext(
            config=self.config,
            cards=self.cards,
            state=self.state,
            event_bus=self.event_bus,
            phase_name=phase_name,
        )
        handler = getattr(self, f"_phase_{phase_name}", self._phase_stub)
        handler(context)

    def _phase_start_round(self, context: PhaseContext) -> None:
        self.effect_engine.trigger("on_round_start", state=context.state)
        context.event_bus.emit(
            EventType.WARNING,
            {
                "phase": context.phase_name,
                "message": "Stub phase: start-of-round mechanics are not implemented yet.",
            },
        )

    def _phase_draft(self, context: PhaseContext) -> None:
        draft = context.config.draft
        if not draft.enabled:
            context.event_bus.emit(EventType.WARNING, {"phase": context.phase_name, "message": "Draft is disabled."})
            return

        ordered_player_ids = self._ordered_player_ids(draft.direction)
        context.event_bus.emit(
            EventType.DRAFT_STARTED,
            {
                "draw_count": draft.draw_count,
                "pick_count": draft.pick_count,
                "pass_count": draft.pass_count,
                "last_player_discard_count": draft.last_player_discard_count,
                "direction": draft.direction,
                "player_order": ordered_player_ids,
            },
        )

        drafted_count = 0
        discarded_count = 0
        for player_id in ordered_player_ids:
            draft_pack = self._draw_draft_pack(player_id, draft.draw_count, context)
            if not draft_pack:
                continue

            picked_cards = self._choose_draft_picks(player_id, draft_pack, draft.pick_count, context)
            for card_id in picked_cards:
                context.state.players[player_id].hand.append(card_id)
                drafted_count += 1
                context.event_bus.emit(
                    EventType.CARD_DRAFTED,
                    {
                        "player_id": player_id,
                        "card_id": card_id,
                        "reason": "bot_pick",
                    },
                )

            remaining_cards = [card_id for card_id in draft_pack if card_id not in picked_cards]
            for card_id in remaining_cards:
                context.state.deck.discard_pile.append(card_id)
                discarded_count += 1
                context.event_bus.emit(
                    EventType.CARD_DISCARDED,
                    {
                        "player_id": player_id,
                        "card_id": card_id,
                        "reason": "mvp_draft_remainder_discarded",
                        "future_pass_count": draft.pass_count,
                    },
                )

        context.event_bus.emit(
            EventType.DRAFT_FINISHED,
            {
                "drafted_count": drafted_count,
                "discarded_count": discarded_count,
                "remaining_deck_count": len(context.state.deck.draw_pile),
                "discard_pile_count": len(context.state.deck.discard_pile),
                "note": "MVP draft discards unpicked cards; pass structure is represented in config and events.",
            },
        )

    def _phase_media_mogul_phase(self, context: PhaseContext) -> None:
        media_mogul_player_id = context.state.round.media_mogul_player_id
        bot = self.bots[media_mogul_player_id]
        bot_context = self.legal_actions.build_context(context.state, media_mogul_player_id)
        options, drawn_cards = self._media_mogul_options(context, bot_context)
        if not options:
            context.event_bus.emit(
                EventType.WARNING,
                {
                    "phase": context.phase_name,
                    "player_id": media_mogul_player_id,
                    "message": "Media mogul has no suitable propaganda or hybrid card in hand.",
                    "allowed_card_types": context.config.propaganda.media_mogul_card_types,
                },
            )
            return

        decision = bot.choose_media_mogul_card(bot_context, options, context.state.rng)
        if decision.choice not in options:
            context.event_bus.emit(
                EventType.WARNING,
                {
                    "phase": context.phase_name,
                    "player_id": media_mogul_player_id,
                    "message": "Bot returned illegal media mogul card choice; phase skipped.",
                    "choice": decision.choice,
                    "legal_options": options,
                },
            )
            return

        chosen_card_id = decision.choice
        source = "deck_draw" if chosen_card_id in drawn_cards else "hand"
        if source == "hand":
            context.state.players[media_mogul_player_id].hand.remove(chosen_card_id)
        for card_id in drawn_cards:
            if card_id != chosen_card_id:
                context.state.deck.discard_pile.append(card_id)
        removed_card_id = context.state.propaganda_track.place_card(chosen_card_id)
        if removed_card_id is not None:
            context.state.deck.discard_pile.append(removed_card_id)
            context.event_bus.emit(
                EventType.PROPAGANDA_REMOVED,
                {
                    "card_id": removed_card_id,
                    "reason": "overflow_remove_oldest",
                    "overflow": context.state.propaganda_track.overflow,
                    "slots": context.state.propaganda_track.get_slots(),
                },
            )
        context.event_bus.emit(
            EventType.PROPAGANDA_PLACED,
            {
                "player_id": media_mogul_player_id,
                "card_id": chosen_card_id,
                "reason": decision.reason,
                "source": source,
                "slots": context.state.propaganda_track.get_slots(),
            },
        )
        context.event_bus.emit(
            EventType.MEDIA_MOGUL_ACTION_TAKEN,
            {
                "player_id": media_mogul_player_id,
                "card_id": chosen_card_id,
                "source": source,
                "drawn_card_ids": drawn_cards,
                "options": options,
                "reason": decision.reason,
                "slots": context.state.propaganda_track.get_slots(),
            },
        )
        self._emit_prop_combo_if_detected(context, chosen_card_id)

    def _phase_journalist_phase(self, context: PhaseContext) -> None:
        journalist_player_id = context.state.round.journalist_player_id
        bot = self.bots[journalist_player_id]
        bot_context = self.legal_actions.build_context(context.state, journalist_player_id)
        options = self.legal_actions.journalist_action_options(bot_context)
        decision = bot.choose_journalist_action(bot_context, options, context.state.rng)
        if decision.choice not in options:
            context.event_bus.emit(
                EventType.WARNING,
                {
                    "phase": context.phase_name,
                    "player_id": journalist_player_id,
                    "message": "Bot returned illegal journalist action; phase skipped.",
                    "choice": decision.choice,
                    "legal_options": options,
                },
            )
            return

        choice = decision.choice
        if not isinstance(choice, dict) or choice.get("action") == "pass":
            context.event_bus.emit(
                EventType.JOURNALIST_ACTION_TAKEN,
                {"player_id": journalist_player_id, "action": "pass", "reason": decision.reason},
            )
            return

        action = choice["action"]
        card_id = choice["card_id"]
        removed_card_id = context.state.propaganda_track.remove_card(card_id)
        if removed_card_id is None:
            context.event_bus.emit(EventType.WARNING, {"player_id": journalist_player_id, "message": "Journalist target missing.", "choice": choice})
            return

        destination = "discard_pile"
        if action == "discard_one":
            context.state.deck.discard_pile.append(removed_card_id)
        elif action == "place_one_on_top_of_deck":
            context.state.deck.draw_pile.insert(0, removed_card_id)
            destination = "top_of_deck"
        elif action == "remove_one_propaganda_card_from_game":
            destination = "removed_from_game"
        else:
            context.event_bus.emit(EventType.WARNING, {"player_id": journalist_player_id, "message": f"Unknown journalist action: {action}"})
            return

        context.event_bus.emit(
            EventType.PROPAGANDA_REMOVED,
            {
                "card_id": removed_card_id,
                "reason": f"journalist_{action}",
                "slots": context.state.propaganda_track.get_slots(),
            },
        )
        context.event_bus.emit(
            EventType.JOURNALIST_ACTION_TAKEN,
            {
                "player_id": journalist_player_id,
                "action": action,
                "card_id": removed_card_id,
                "destination": destination,
                "reason": decision.reason,
                "slots": context.state.propaganda_track.get_slots(),
            },
        )

    def _media_mogul_options(self, context: PhaseContext, bot_context: BotContext) -> tuple[list[str], list[str]]:
        allowed_types = set(context.config.propaganda.media_mogul_card_types)
        source_mode = context.config.propaganda.allowed_sources_for_propaganda
        options: list[str] = []
        drawn_cards: list[str] = []

        if source_mode in {"hand", "both"}:
            options.extend(self.legal_actions.media_mogul_card_options(bot_context))
        if source_mode in {"deck_draw", "both"}:
            drawn_cards = self._draw_media_mogul_candidates(context, allowed_types)
            options.extend(drawn_cards)

        unique_options = list(dict.fromkeys(options))
        return unique_options[: context.config.propaganda.media_mogul_choice_count], drawn_cards

    def _draw_media_mogul_candidates(self, context: PhaseContext, allowed_types: set[str]) -> list[str]:
        drawn_cards: list[str] = []
        inspected_cards: list[str] = []
        for _ in range(context.config.propaganda.media_mogul_draw_count):
            if not context.state.deck.draw_pile:
                break
            card_id = context.state.deck.draw_pile.pop(0)
            inspected_cards.append(card_id)
            if card_id in self.cards_by_id and self.cards_by_id[card_id].type in allowed_types:
                drawn_cards.append(card_id)
            else:
                context.state.deck.discard_pile.append(card_id)
        if inspected_cards:
            context.event_bus.emit(
                EventType.CARD_DRAWN,
                {
                    "player_id": context.state.round.media_mogul_player_id,
                    "card_ids": inspected_cards,
                    "destination": "media_mogul_choices",
                    "reason": "media_mogul_draw",
                },
            )
        return drawn_cards

    def _emit_prop_combo_if_detected(self, context: PhaseContext, chosen_card_id: str) -> None:
        chosen = self.cards_by_id[chosen_card_id]
        slots = context.state.propaganda_track.get_slots()
        matching_faction_count = sum(
            1
            for card_id in slots
            if card_id is not None
            and card_id in self.cards_by_id
            and self.cards_by_id[card_id].faction == chosen.faction
            and chosen.faction is not None
        )
        matching_tag_count = sum(
            1
            for card_id in slots
            if card_id is not None and card_id in self.cards_by_id and set(self.cards_by_id[card_id].tags).intersection(chosen.tags)
        )
        if matching_faction_count >= 2 or matching_tag_count >= 2:
            context.event_bus.emit(
                EventType.PROPAGANDA_COMBO_DETECTED,
                {
                    "card_id": chosen_card_id,
                    "matching_faction_count": matching_faction_count,
                    "matching_tag_count": matching_tag_count,
                    "slots": slots,
                },
            )

    def _phase_planning(self, context: PhaseContext) -> None:
        context.state.planned_actions.clear()
        context.state.revealed_actions.clear()
        context.state.resolved_actions.clear()

        for player_id in self._ordered_player_ids("clockwise"):
            bot = self.bots[player_id]
            bot_context = self.legal_actions.build_context(context.state, player_id)

            commit_options = self.legal_actions.cards_to_commit_options(bot_context)
            commit_decision = bot.choose_cards_to_commit(bot_context, commit_options, context.state.rng)
            committed_card_ids = list(commit_decision.choice if commit_decision.choice in commit_options else [])
            legal_hand = set(context.state.players[player_id].hand)
            committed_card_ids = [card_id for card_id in committed_card_ids if card_id in legal_hand]
            for card_id in committed_card_ids:
                context.state.players[player_id].hand.remove(card_id)

            action_type_options = self.legal_actions.action_type_options(bot_context)
            action_type_decision = bot.choose_action_type(bot_context, action_type_options, context.state.rng)
            action_type = action_type_decision.choice if action_type_decision.choice in action_type_options else "support"

            acting_options = self.legal_actions.acting_faction_options(bot_context)
            acting_decision = bot.choose_acting_faction(bot_context, acting_options, context.state.rng)
            acting_faction_id = acting_decision.choice if acting_decision.choice in acting_options else acting_options[0]

            target_options = self.legal_actions.target_faction_options(bot_context)
            target_decision = bot.choose_target_faction(bot_context, target_options, context.state.rng)
            target_faction_id = target_decision.choice if target_decision.choice in target_options else target_options[0]

            planned_action = PlannedAction(
                player_id=player_id,
                committed_card_ids=committed_card_ids,
                action_type=action_type,
                acting_faction_id=acting_faction_id,
                target_faction_id=target_faction_id,
            )
            context.state.planned_actions[player_id] = planned_action
            context.event_bus.emit(
                EventType.ACTION_COMMITTED,
                {
                    "player_id": player_id,
                    "committed_count": len(committed_card_ids),
                    "action_type": action_type,
                    "acting_faction_id": acting_faction_id,
                    "target_faction_id": target_faction_id,
                    "commit_reason": commit_decision.reason,
                    "action_type_reason": action_type_decision.reason,
                    "acting_faction_reason": acting_decision.reason,
                    "target_faction_reason": target_decision.reason,
                },
            )

    def _phase_reveal(self, context: PhaseContext) -> None:
        context.state.revealed_actions.clear()
        ordered_actions = sorted(
            context.state.planned_actions.values(),
            key=lambda action: (action.initiative_count, self._initiative_tiebreaker_index(action.player_id)),
        )
        for reveal_order, action in enumerate(ordered_actions):
            revealed_action = RevealedAction(
                player_id=action.player_id,
                committed_card_ids=list(action.committed_card_ids),
                action_type=action.action_type,
                acting_faction_id=action.acting_faction_id,
                target_faction_id=action.target_faction_id,
                strength=self._action_strength(action.committed_card_ids),
                initiative_count=action.initiative_count,
                reveal_order=reveal_order,
            )
            self.effect_engine.trigger("on_reveal", state=context.state, revealed_action=revealed_action)
            context.state.revealed_actions.append(revealed_action)
            context.event_bus.emit(EventType.ACTION_REVEALED, revealed_action.model_dump())

    def _phase_action_resolution(self, context: PhaseContext) -> None:
        for revealed_action in context.state.revealed_actions:
            if self.ended_by is not None:
                break
            self.effect_engine.counters["prevent_population_loss"] = 0
            self.effect_engine.trigger(
                "before_action_resolution",
                state=context.state,
                revealed_action=revealed_action,
                target_faction_id=revealed_action.target_faction_id,
            )
            self.effect_engine.trigger(
                "when_in_propaganda_before_action_resolution",
                state=context.state,
                revealed_action=revealed_action,
                target_faction_id=revealed_action.target_faction_id,
            )
            resolved_action = self._resolve_action(context, revealed_action)
            context.state.resolved_actions.append(resolved_action)
            for card_id in revealed_action.committed_card_ids:
                context.state.deck.discard_pile.append(card_id)
                context.event_bus.emit(
                    EventType.CARD_DISCARDED,
                    {"player_id": revealed_action.player_id, "card_id": card_id, "reason": "action_resolved"},
                )
            context.event_bus.emit(EventType.ACTION_RESOLVED, resolved_action.model_dump())
            self.effect_engine.trigger(
                "after_action_resolution",
                state=context.state,
                revealed_action=revealed_action,
                target_faction_id=revealed_action.target_faction_id,
            )
            if "after_population_change" in self.config.victory.check_timing:
                self._check_victory("after_population_change")

    def _phase_victory_check(self, context: PhaseContext) -> None:
        self._check_victory("end_of_round")
        self.effect_engine.trigger("on_round_end", state=context.state)

    def _ordered_player_ids(self, direction: str) -> list[str]:
        ordered = [player.id for player in sorted(self.config.players, key=lambda player: player.seat)]
        if direction == "counterclockwise":
            return list(reversed(ordered))
        return ordered

    def _initiative_tiebreaker_index(self, player_id: str) -> int:
        ordered = self._ordered_player_ids("clockwise")
        start_index = ordered.index(self.state.round.start_player_id)
        return ordered[start_index:].index(player_id) if player_id in ordered[start_index:] else len(ordered[start_index:]) + ordered[:start_index].index(player_id)

    def _action_strength(self, committed_card_ids: list[str]) -> int:
        return sum(self.cards_by_id[card_id].strength for card_id in committed_card_ids if card_id in self.cards_by_id)

    def _resolve_action(self, context: PhaseContext, revealed_action: RevealedAction) -> ResolvedAction:
        faction = context.state.factions[revealed_action.target_faction_id]
        population_before = faction.population
        neutral_before = context.state.neutral_population
        self.effect_engine.trigger(
            "before_population_change",
            state=context.state,
            revealed_action=revealed_action,
            target_faction_id=revealed_action.target_faction_id,
        )
        if revealed_action.action_type == "support":
            applied_delta = min(revealed_action.strength, context.state.neutral_population)
            faction.population += applied_delta
            context.state.neutral_population -= applied_delta
        else:
            loss = max(0, revealed_action.strength - self.effect_engine.counters.get("prevent_population_loss", 0))
            applied_delta = -min(loss, faction.population)
            faction.population += applied_delta

        resolved_action = ResolvedAction(
            player_id=revealed_action.player_id,
            action_type=revealed_action.action_type,
            target_faction_id=revealed_action.target_faction_id,
            strength=revealed_action.strength,
            population_before=population_before,
            population_after=faction.population,
            neutral_before=neutral_before,
            neutral_after=context.state.neutral_population,
            applied_delta=applied_delta,
        )
        context.event_bus.emit(
            EventType.POPULATION_CHANGED,
            {
                "player_id": revealed_action.player_id,
                "action_type": revealed_action.action_type,
                "target_faction_id": revealed_action.target_faction_id,
                "population_before": population_before,
                "population_after": faction.population,
                "neutral_before": neutral_before,
                "neutral_after": context.state.neutral_population,
                "applied_delta": applied_delta,
            },
        )
        self.effect_engine.trigger(
            "after_population_change",
            state=context.state,
            revealed_action=revealed_action,
            target_faction_id=revealed_action.target_faction_id,
            population_delta=applied_delta,
        )
        return resolved_action

    def _check_victory(self, timing: str) -> VictoryResult:
        self.effect_engine.trigger("on_victory_check", state=self.state)
        result = self.victory_checker.check(self.state, timing)
        self.event_bus.set_context(round_number=self.state.round.round_number, phase=self.state.round.phase)
        self.event_bus.emit(EventType.VICTORY_CHECKED, result.model_dump())
        if result.is_win and result.winner_type != "none":
            self.ended_by = "max_rounds" if timing == "game_end" else "victory"
            self.winner_type = result.winner_type
            self.winner_player = result.winner_player
            self.winner_faction = result.winner_faction
            self.winning_condition = result.winning_condition
            self.tie_info = result.tie_info
            self.event_bus.emit(
                EventType.GAME_ENDED,
                {
                    "ended_by": self.ended_by,
                    "winner_type": self.winner_type,
                    "winner_player": self.winner_player,
                    "winner_faction": self.winner_faction,
                    "winning_condition": self.winning_condition,
                    "tie_info": self.tie_info,
                    "checked_timing": timing,
                },
            )
        return result

    def _draw_draft_pack(self, player_id: str, draw_count: int, context: PhaseContext) -> list[str]:
        draft_pack: list[str] = []
        for _ in range(draw_count):
            if not context.state.deck.draw_pile:
                context.event_bus.emit(
                    EventType.WARNING,
                    {
                        "player_id": player_id,
                        "message": "Draft deck is empty before configured draw_count was reached.",
                    },
                )
                break
            card_id = context.state.deck.draw_pile.pop(0)
            draft_pack.append(card_id)
            context.event_bus.emit(
                EventType.CARD_DRAWN,
                {
                    "player_id": player_id,
                    "card_id": card_id,
                    "destination": "draft_pack",
                    "reason": "draft",
                },
            )
        return draft_pack

    def _choose_draft_picks(
        self,
        player_id: str,
        draft_pack: list[str],
        pick_count: int,
        context: PhaseContext,
    ) -> list[str]:
        picked_cards: list[str] = []
        bot = self.bots[player_id]
        for _ in range(min(pick_count, len(draft_pack))):
            options = [card_id for card_id in draft_pack if card_id not in picked_cards]
            if not options:
                break
            base_context = self.legal_actions.build_context(context.state, player_id)
            bot_context = BotContext(
                player_id=base_context.player_id,
                public_view=base_context.public_view,
                rules=base_context.rules,
                cards_by_id=base_context.cards_by_id,
            )
            decision = bot.choose_draft_pick(bot_context, options, context.state.rng)
            if decision.choice not in options:
                context.event_bus.emit(
                    EventType.WARNING,
                    {
                        "player_id": player_id,
                        "message": "Bot returned illegal draft pick; ignored.",
                        "choice": decision.choice,
                        "legal_options": options,
                    },
                )
                continue
            picked_cards.append(decision.choice)
        return picked_cards

    def _phase_stub(self, context: PhaseContext) -> None:
        context.event_bus.emit(
            EventType.WARNING,
            {
                "phase": context.phase_name,
                "message": f"Stub phase: {context.phase_name} is not implemented yet.",
            },
        )

    def _build_result(self) -> GameResult:
        return GameResult(
            game_id=self.config.game_id,
            seed=self.seed,
            rounds_played=self.state.round.round_number,
            ended_by=self.ended_by or "max_rounds",
            winner_type=self.winner_type,
            winner_player=self.winner_player,
            winner_faction=self.winner_faction,
            winning_condition=self.winning_condition,
            tie_info=self.tie_info,
            final_populations={key: faction.population for key, faction in self.state.factions.items()},
            event_count=len(self.state.event_log.events),
        )
