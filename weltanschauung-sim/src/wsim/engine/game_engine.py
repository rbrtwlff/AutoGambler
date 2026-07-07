from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Literal

from pydantic import BaseModel

from wsim.bots import BotContext, BotFactory, LegalActionProvider
from wsim.core.events import EventBus, EventType
from wsim.core.models import BotConfig, CardConfig, GameConfig
from wsim.core.state import GameState, PlannedAction, ResolvedAction, RevealedAction
from wsim.engine.effects import EffectEngine
from wsim.engine.invariants import validate_game_result_matches_events, validate_game_state
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
        self.cards_by_id = self._build_card_lookup(cards)
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
        self.victory_checker = VictoryChecker(config)
        self.effect_engine = EffectEngine(config, cards, self.event_bus)
        self.effect_engine.cards_by_id.update(self.cards_by_id)
        self._validate_state_if_strict()

    def run_game(self) -> GameResult:
        while self.ended_by is None and self.state.round.round_number < self.config.round_flow.max_rounds:
            self.run_round()

        if self.ended_by is None and not self._is_v03_rules():
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

        result = self._build_result()
        if self.config.quality.strict_mode:
            validate_game_result_matches_events(result, self.state)
        return result

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
        self._validate_state_if_strict()

    def _phase_start_round(self, context: PhaseContext) -> None:
        self.effect_engine.trigger("on_round_start", state=context.state)
        context.event_bus.emit(
            EventType.WARNING,
            {
                "phase": context.phase_name,
                "message": "Stub phase: start-of-round mechanics are not implemented yet.",
            },
        )

    def _phase_round_start(self, context: PhaseContext) -> None:
        self.effect_engine.trigger("on_round_start", state=context.state)
        context.event_bus.emit(
            EventType.ROUND_START_SNAPSHOT,
            {
                "start_player_id": context.state.round.start_player_id,
                "journalist_player_id": context.state.round.journalist_player_id,
                "media_mogul_player_id": context.state.round.media_mogul_player_id,
                "populations": {key: faction.population for key, faction in context.state.factions.items()},
                "neutral_population": context.state.neutral_population,
                "propaganda_slots": context.state.propaganda_track.get_slots(),
                "players": {
                    player_id: {
                        "source_count": len(player.sources),
                        "hand_size": len(player.hand),
                        "research_assignment_count": len(player.hidden_research_orders),
                    }
                    for player_id, player in context.state.players.items()
                },
            },
        )

    def _phase_journalist_check(self, context: PhaseContext) -> None:
        journalist_id = context.state.round.journalist_player_id
        bids: dict[str, int] = {}
        for player_id in self._ordered_player_ids_from_start():
            player = context.state.players[player_id]
            options = list(range(len(player.sources) + 1))
            bot_context = self.legal_actions.build_context(context.state, player_id)
            decision = self.bots[player_id].how_many_sources_to_bid(bot_context, options, context.state.rng)
            bid = int(decision.choice) if decision.choice in options else 0
            spent = player.sources[:bid]
            del player.sources[:bid]
            bids[player_id] = bid
            for source_id in spent:
                context.event_bus.emit(
                    EventType.SOURCE_SPENT,
                    {
                        "player_id": player_id,
                        "source_id": source_id,
                        "purpose": "journalist_defense" if player_id == journalist_id else "journalist_challenge",
                        "reason": decision.reason,
                    },
                )

        journalist_bid = bids.get(journalist_id, 0)
        challenger_total = sum(value for player_id, value in bids.items() if player_id != journalist_id)
        new_journalist_id = journalist_id
        changed = False
        if challenger_total > journalist_bid:
            challenger_bids = {player_id: bid for player_id, bid in bids.items() if player_id != journalist_id}
            highest_bid = max(challenger_bids.values(), default=0)
            candidates = [player_id for player_id, bid in challenger_bids.items() if bid == highest_bid]
            new_journalist_id = self._tiebreak_players(candidates)
            changed = new_journalist_id != journalist_id
            if changed:
                context.state.players[journalist_id].roles = [
                    role for role in context.state.players[journalist_id].roles if role != "journalist"
                ]
                if "journalist" not in context.state.players[new_journalist_id].roles:
                    context.state.players[new_journalist_id].roles.append("journalist")
                context.state.round.journalist_player_id = new_journalist_id
                context.event_bus.emit(
                    EventType.JOURNALIST_CHANGED,
                    {
                        "old_journalist_player_id": journalist_id,
                        "new_journalist_player_id": new_journalist_id,
                        "bids": bids,
                        "journalist_bid": journalist_bid,
                        "challenger_total": challenger_total,
                    },
                )

        context.event_bus.emit(
            EventType.JOURNALIST_CHECKED,
            {
                "journalist_player_id": context.state.round.journalist_player_id,
                "previous_journalist_player_id": journalist_id,
                "bids": bids,
                "journalist_bid": journalist_bid,
                "challenger_total": challenger_total,
                "changed": changed,
            },
        )

    def _phase_media_mogul_election(self, context: PhaseContext) -> None:
        player_ids = self._ordered_player_ids_from_start()
        votes: dict[str, str] = {}
        context.event_bus.emit(EventType.MEDIA_MOGUL_ELECTION_STARTED, {"candidates": player_ids})
        for player_id in player_ids:
            bot_context = self.legal_actions.build_context(context.state, player_id)
            decision = self.bots[player_id].which_player_to_vote_for(bot_context, player_ids, context.state.rng)
            vote = str(decision.choice) if decision.choice in player_ids else player_id
            votes[player_id] = vote
            context.event_bus.emit(
                EventType.MEDIA_MOGUL_VOTE_CAST,
                {"player_id": player_id, "vote_for": vote, "public": True, "reason": decision.reason},
            )

        counts = {candidate: list(votes.values()).count(candidate) for candidate in player_ids}
        highest = max(counts.values(), default=0)
        tied = [player_id for player_id, count in counts.items() if count == highest]
        if len(tied) == 1:
            winner = tied[0]
            tie_breaker = None
        elif context.state.round.journalist_player_id in tied:
            winner = context.state.round.start_player_id
            tie_breaker = "start_player_because_journalist_tied"
        else:
            winner = context.state.round.journalist_player_id
            tie_breaker = "journalist"

        old_media_mogul = context.state.round.media_mogul_player_id
        context.state.players[old_media_mogul].roles = [
            role for role in context.state.players[old_media_mogul].roles if role != "media_mogul"
        ]
        if "media_mogul" not in context.state.players[winner].roles:
            context.state.players[winner].roles.append("media_mogul")
        context.state.round.media_mogul_player_id = winner
        context.event_bus.emit(
            EventType.MEDIA_MOGUL_CHANGED,
            {
                "old_media_mogul_player_id": old_media_mogul,
                "new_media_mogul_player_id": winner,
                "votes": votes,
                "counts": counts,
                "tied_candidates": tied,
                "tie_breaker": tie_breaker,
            },
        )

    def _phase_draft(self, context: PhaseContext) -> None:
        if self._is_v03_rules():
            self._phase_draft_v03(context)
            return
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
                context.state.deck.discard_card(card_id, event_bus=context.event_bus, reason="mvp_draft_remainder_discarded", player_id=player_id)
                discarded_count += 1

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

    def _phase_draft_v03(self, context: PhaseContext) -> None:
        context.state.draft_pool.clear()
        context.event_bus.emit(
            EventType.DRAFT_STARTED,
            {
                "mode": "v0_3",
                "contribution_cards_per_player": 1,
                "start_player_draws_extra_cards": 2,
                "cards_seen_each_pick": 3,
                "cards_taken_each_pick": 1,
            },
        )
        order = self._ordered_player_ids_from_start()
        contributions: dict[str, str] = {}
        for player_id in order:
            player = context.state.players[player_id]
            if not player.hand:
                drawn = context.state.deck.draw_cards(
                    1,
                    config=context.config.deck,
                    rng=context.state.rng,
                    event_bus=context.event_bus,
                    reason="v03_forced_draft_contribution",
                    player_id=player_id,
                    destination="hand",
                    game_id=context.config.game_id,
                    round_number=context.state.round.round_number,
                    phase=context.phase_name,
                )
                player.hand.extend(drawn)
            if not player.hand:
                context.event_bus.emit(EventType.WARNING, {"player_id": player_id, "message": "No card available for required draft contribution."})
                continue
            bot_context = self.legal_actions.build_context(context.state, player_id)
            decision = self.bots[player_id].choose_card_to_contribute_to_draft(bot_context, list(player.hand), context.state.rng)
            contribution = str(decision.choice) if decision.choice in player.hand else player.hand[0]
            player.hand.remove(contribution)
            contributions[player_id] = contribution
            context.event_bus.emit(
                EventType.DRAFT_CONTRIBUTED,
                {"player_id": player_id, "card_id": contribution, "instance_id": contribution, "face_down": True, "reason": decision.reason},
            )

        pool: list[str] = []
        start_player_id = context.state.round.start_player_id
        if start_player_id in contributions:
            pool.append(contributions[start_player_id])
        extra_cards = context.state.deck.draw_cards(
            2,
            config=context.config.deck,
            rng=context.state.rng,
            event_bus=context.event_bus,
            reason="v03_start_player_extra_draft_cards",
            player_id=start_player_id,
            destination="draft_pool",
            game_id=context.config.game_id,
            round_number=context.state.round.round_number,
            phase=context.phase_name,
        )
        pool.extend(extra_cards)

        picked_count = 0
        for player_id in order:
            if player_id != start_player_id and player_id in contributions:
                pool.append(contributions[player_id])
            while len(pool) < 3:
                drawn = context.state.deck.draw_cards(
                    1,
                    config=context.config.deck,
                    rng=context.state.rng,
                    event_bus=context.event_bus,
                    reason="v03_draft_pool_refill",
                    player_id=player_id,
                    destination="draft_pool",
                    game_id=context.config.game_id,
                    round_number=context.state.round.round_number,
                    phase=context.phase_name,
                )
                if not drawn:
                    break
                pool.extend(drawn)
            if not pool:
                continue
            options = list(pool)
            bot_context = self.legal_actions.build_context(context.state, player_id)
            decision = self.bots[player_id].choose_card_to_take_from_three(bot_context, options, context.state.rng)
            picked = str(decision.choice) if decision.choice in options else options[0]
            pool.remove(picked)
            context.state.players[player_id].hand.append(picked)
            picked_count += 1
            context.event_bus.emit(
                EventType.CARD_DRAFTED,
                {
                    "player_id": player_id,
                    "card_id": picked,
                    "instance_id": picked,
                    "seen_card_ids": options,
                    "passed_card_ids": list(pool),
                    "reason": decision.reason,
                },
            )
            context.event_bus.emit(EventType.DRAFT_PASSED, {"player_id": player_id, "passed_card_ids": list(pool)})

        context.state.journalist_pool.extend(pool)
        context.event_bus.emit(
            EventType.DRAFT_FINISHED,
            {
                "mode": "v0_3",
                "drafted_count": picked_count,
                "journalist_pool_card_ids": list(pool),
                "contribution_count": len(contributions),
            },
        )

    def _phase_journalist_and_media_mogul(self, context: PhaseContext) -> None:
        journalist_id = context.state.round.journalist_player_id
        media_mogul_id = context.state.round.media_mogul_player_id
        extra = context.state.deck.draw_cards(
            1,
            config=context.config.deck,
            rng=context.state.rng,
            event_bus=context.event_bus,
            reason="v03_journalist_extra_card",
            player_id=journalist_id,
            destination="journalist_pool",
            game_id=context.config.game_id,
            round_number=context.state.round.round_number,
            phase=context.phase_name,
        )
        context.state.journalist_pool.extend(extra)
        context.event_bus.emit(
            EventType.JOURNALIST_POOL_CREATED,
            {"player_id": journalist_id, "card_ids": list(context.state.journalist_pool), "pool_size": len(context.state.journalist_pool)},
        )
        if not context.state.journalist_pool:
            context.event_bus.emit(EventType.WARNING, {"phase": context.phase_name, "message": "Journalist pool is empty."})
            return

        action_options = ["future_set"]
        prop_cards = [card_id for card_id in context.state.propaganda_track.get_slots() if card_id is not None]
        if prop_cards and len(context.state.journalist_pool) >= 1:
            action_options.append("remove_propaganda")
        bot_context = self.legal_actions.build_context(context.state, journalist_id)
        action_decision = self.bots[journalist_id].choose_future_set_or_remove_propaganda(
            bot_context,
            action_options,
            context.state.rng,
        )
        action = str(action_decision.choice) if action_decision.choice in action_options else "future_set"

        if action == "remove_propaganda" and prop_cards:
            remove_decision = self.bots[journalist_id].choose_propaganda_to_remove(bot_context, prop_cards, context.state.rng)
            target_prop = str(remove_decision.choice) if remove_decision.choice in prop_cards else prop_cards[0]
            removed = context.state.propaganda_track.remove_card(target_prop)
            if removed is not None:
                context.state.deck.discard_card(removed, event_bus=context.event_bus, reason="v03_journalist_remove_propaganda", player_id=journalist_id)
                context.event_bus.emit(EventType.PROPAGANDA_REMOVED, {"card_id": removed, "reason": remove_decision.reason, "slots": context.state.propaganda_track.get_slots()})
            cost_decision = self.bots[journalist_id].choose_pool_card_to_discard_as_cost(
                bot_context,
                list(context.state.journalist_pool),
                context.state.rng,
            )
            cost_card = str(cost_decision.choice) if cost_decision.choice in context.state.journalist_pool else context.state.journalist_pool[0]
            context.state.journalist_pool.remove(cost_card)
            context.state.deck.discard_card(cost_card, event_bus=context.event_bus, reason="v03_journalist_pool_cost", player_id=journalist_id)
            context.event_bus.emit(
                EventType.JOURNALIST_ACTION_TAKEN,
                {
                    "player_id": journalist_id,
                    "action": action,
                    "removed_propaganda_card_id": removed,
                    "discarded_pool_card_id": cost_card,
                    "action_reason": action_decision.reason,
                    "remove_reason": remove_decision.reason,
                    "cost_reason": cost_decision.reason,
                },
            )
        else:
            top_decision = self.bots[journalist_id].choose_card_to_put_on_top_of_draw_deck(
                bot_context,
                list(context.state.journalist_pool),
                context.state.rng,
            )
            top_card = str(top_decision.choice)
            if top_card not in context.state.journalist_pool:
                top_card = context.state.journalist_pool[0]
            context.state.journalist_pool.remove(top_card)
            context.state.deck.draw_pile.insert(0, top_card)
            context.event_bus.emit(
                EventType.CARD_MOVED_TO_TOP_OF_DECK,
                {"player_id": journalist_id, "card_id": top_card, "instance_id": top_card, "reason": top_decision.reason},
            )
            context.event_bus.emit(
                EventType.JOURNALIST_ACTION_TAKEN,
                {"player_id": journalist_id, "action": "future_set", "action_reason": action_decision.reason, "top_card_reason": top_decision.reason},
            )

        context.state.media_mogul_pool.extend(context.state.journalist_pool[:2])
        context.state.journalist_pool.clear()
        if not context.state.media_mogul_pool:
            context.event_bus.emit(EventType.WARNING, {"phase": context.phase_name, "message": "Media mogul received no cards."})
            return
        media_context = self.legal_actions.build_context(context.state, media_mogul_id)
        media_options = list(context.state.media_mogul_pool)
        media_decision = self.bots[media_mogul_id].choose_one_of_two_as_new_propaganda(media_context, media_options, context.state.rng)
        chosen = str(media_decision.choice) if media_decision.choice in media_options else media_options[0]
        context.state.media_mogul_pool.remove(chosen)
        displaced = context.state.propaganda_track.place_card_newest(chosen)
        if displaced is not None:
            context.state.deck.discard_card(displaced, event_bus=context.event_bus, reason="v03_propaganda_displaced")
            context.event_bus.emit(EventType.PROPAGANDA_REMOVED, {"card_id": displaced, "reason": "v03_slot_4_displaced", "slots": context.state.propaganda_track.get_slots()})
            self._apply_v03_displaced_propaganda_effect(displaced, context)
        for unchosen in list(context.state.media_mogul_pool):
            context.state.deck.discard_card(unchosen, event_bus=context.event_bus, reason="v03_media_mogul_unchosen", player_id=media_mogul_id)
        context.state.media_mogul_pool.clear()
        context.event_bus.emit(EventType.PROPAGANDA_PLACED, {"player_id": media_mogul_id, "card_id": chosen, "reason": media_decision.reason, "slots": context.state.propaganda_track.get_slots()})
        context.event_bus.emit(EventType.MEDIA_MOGUL_ACTION_TAKEN, {"player_id": media_mogul_id, "card_id": chosen, "reason": media_decision.reason})

    def _phase_discussion(self, context: PhaseContext) -> None:
        discussion = context.config.v03.get("discussion", {}) if self._is_v03_rules() else {}
        mode = str(discussion.get("mechanical_effects", "none"))
        context.event_bus.emit(
            EventType.DISCUSSION_HELD,
            {
                "mode": mode,
                "mechanical_effects_applied": False,
                "notes": discussion.get("notes") or "Discussion/Intrigue has no configured automatic mechanics.",
            },
        )

    def _phase_urn(self, context: PhaseContext) -> None:
        context.state.urn.clear()
        for player_id in self._ordered_player_ids_from_start():
            player = context.state.players[player_id]
            if not player.hand:
                continue
            max_count = min(3, len(player.hand))
            bot_context = self.legal_actions.build_context(context.state, player_id)
            count_options = list(range(1, max_count + 1))
            count_decision = self.bots[player_id].choose_number_of_cards_for_urn(bot_context, count_options, context.state.rng)
            chosen_count = int(count_decision.choice) if count_decision.choice in count_options else 1
            options = [list(combo) for combo in combinations(player.hand, chosen_count)]
            decision = self.bots[player_id].choose_cards_for_urn(bot_context, options, context.state.rng)
            chosen_cards = list(decision.choice) if decision.choice in options else options[0]
            for card_id in chosen_cards:
                if card_id in player.hand:
                    player.hand.remove(card_id)
                    context.state.urn.append(card_id)
                    context.event_bus.emit(
                        EventType.URN_CARD_SUBMITTED,
                        {
                            "player_id": player_id,
                            "card_id": card_id,
                            "instance_id": card_id,
                            "anonymous": True,
                            "count_reason": count_decision.reason,
                            "reason": decision.reason,
                        },
                    )
        before_shuffle = list(context.state.urn)
        context.state.rng.shuffle(context.state.urn)
        context.event_bus.emit(EventType.URN_SHUFFLED, {"card_count": len(context.state.urn), "changed_order": before_shuffle != context.state.urn})

    def _phase_world_history_and_combat(self, context: PhaseContext) -> None:
        context.state.world_history_row = list(context.state.urn)
        context.state.urn.clear()
        row = list(context.state.world_history_row)
        context.event_bus.emit(EventType.WORLD_HISTORY_REVEALED, {"card_ids": row, "order_relevant": True})
        base_power = self._world_history_base_power(row)
        propaganda_power = self._active_propaganda_power(base_power)
        interpretation_authority = self._v03_interpretation_authority(propaganda_power)
        total_power = {
            faction_id: base_power.get(faction_id, 0) + propaganda_power.get(faction_id, 0)
            for faction_id in context.state.factions
        }
        power_modifiers = self._apply_v03_power_text_effects(row, base_power, propaganda_power, total_power, context)
        context.event_bus.emit(
            EventType.WORLD_HISTORY_POWER_CALCULATED,
            {
                "base_power": base_power,
                "propaganda_power": propaganda_power,
                "interpretation_authority": interpretation_authority,
                "power_modifiers": power_modifiers,
                "total_power": total_power,
            },
        )
        propaganda_effects = self._apply_v03_propaganda_text_effects(row, total_power, context)
        population_effects = self._apply_v03_population_text_effects(row, total_power, context)
        deltas = {faction_id: 0 for faction_id in context.state.factions}
        attack_pairs, target_effects = self._v03_attack_pairs(row, total_power, interpretation_authority, context)
        combat_tiebreakers = self._v03_combat_tiebreakers(row, total_power, context)
        attackers_with_targets = {attacker for attacker, defender in attack_pairs if defender is not None}
        for faction_id, power in total_power.items():
            if power > 0 and faction_id not in attackers_with_targets:
                deltas[faction_id] += 2 if power >= 12 else 1
        attack_records: list[dict[str, Any]] = []
        for attacker, defender in attack_pairs:
            if defender is None or defender not in deltas or attacker == defender:
                continue
            margin = total_power.get(attacker, 0) - total_power.get(defender, 0)
            tie_won = margin == 0 and attacker in combat_tiebreakers
            if margin < 0 or (margin == 0 and not tie_won):
                continue
            base_impact = self._combat_impact(margin, tie_won=tie_won)
            attack_records.append(
                {
                    "attacker": attacker,
                    "defender": defender,
                    "margin": margin,
                    "tie_won": tie_won,
                    "tie_breaker_cards": combat_tiebreakers.get(attacker, []),
                    "base_impact": base_impact,
                    "impact": base_impact,
                    "destroyed_impact": 0,
                    "prevented": False,
                    "modifiers": [],
                }
            )
        combat_modifiers = self._apply_v03_combat_text_modifiers(row, attack_records, total_power, interpretation_authority, context)
        destroyed_by_faction = {faction_id: 0 for faction_id in context.state.factions}
        for record in attack_records:
            impact = max(0, int(record.get("impact", 0)))
            destroyed_impact = min(impact, max(0, int(record.get("destroyed_impact", 0))))
            transferred_impact = min(impact - destroyed_impact, max(0, int(record.get("transferred_impact", 0))))
            record["destroyed_impact"] = destroyed_impact
            record["transferred_impact"] = transferred_impact
            defender = str(record["defender"])
            deltas[defender] -= impact
            destroyed_by_faction[defender] += destroyed_impact
            transfer_target = record.get("transfer_target_faction")
            if transferred_impact and transfer_target in deltas:
                deltas[str(transfer_target)] += transferred_impact
            deltas["neutral"] = deltas.get("neutral", 0) + impact - destroyed_impact - transferred_impact

        before = {faction_id: faction.population for faction_id, faction in context.state.factions.items()}
        neutral_before = context.state.neutral_population
        applied: dict[str, int] = {}
        destroyed_applied: dict[str, int] = {}
        for faction_id, delta in deltas.items():
            if faction_id == "neutral" or delta == 0:
                continue
            faction = context.state.factions[faction_id]
            if delta < 0:
                actual = -min(faction.population, abs(delta))
                faction.population += actual
                context.state.neutral_population -= actual
                destroyed_actual = min(-actual, destroyed_by_faction.get(faction_id, 0))
                if destroyed_actual:
                    context.state.neutral_population -= destroyed_actual
                    destroyed_applied[faction_id] = destroyed_actual
                applied[faction_id] = actual
            else:
                actual = min(context.state.neutral_population, delta)
                faction.population += actual
                context.state.neutral_population -= actual
                applied[faction_id] = actual
        utility_effects = self._apply_v03_utility_text_effects(
            row,
            total_power,
            attack_records,
            applied,
            propaganda_effects,
            population_effects,
            context,
        )
        context.event_bus.emit(
            EventType.COMBAT_RESOLVED,
            {
                "base_power": base_power,
                "propaganda_power": propaganda_power,
                "interpretation_authority": interpretation_authority,
                "total_power": total_power,
                "propaganda_effects": propaganda_effects,
                "population_effects": population_effects,
                "utility_effects": utility_effects,
                "target_effects": target_effects,
                "combat_tiebreakers": combat_tiebreakers,
                "attack_pairs": [{"attacker": attacker, "defender": defender} for attacker, defender in attack_pairs],
                "attack_records": attack_records,
                "combat_modifiers": combat_modifiers,
                "requested_deltas": deltas,
                "applied_deltas": applied,
                "destroyed_applied": destroyed_applied,
                "population_before": before,
                "population_after": {faction_id: faction.population for faction_id, faction in context.state.factions.items()},
                "neutral_before": neutral_before,
                "neutral_after": context.state.neutral_population,
                "simultaneous": True,
            },
        )
        for faction_id, delta in applied.items():
            context.event_bus.emit(
                EventType.POPULATION_CHANGED,
                {
                    "player_id": None,
                    "action_type": "v03_world_history_combat",
                    "target_faction_id": faction_id,
                    "population_before": before[faction_id],
                    "population_after": context.state.factions[faction_id].population,
                    "neutral_before": neutral_before,
                    "neutral_after": context.state.neutral_population,
                    "applied_delta": delta,
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
                context.state.deck.discard_card(card_id, event_bus=context.event_bus, reason="media_mogul_unpicked", player_id=media_mogul_player_id)
        removed_card_id = context.state.propaganda_track.place_card(chosen_card_id)
        if removed_card_id is not None:
            context.state.deck.discard_card(removed_card_id, event_bus=context.event_bus, reason="overflow_remove_oldest")
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
            context.state.deck.discard_card(removed_card_id, event_bus=context.event_bus, reason=f"journalist_{action}", player_id=journalist_player_id)
        elif action == "place_one_on_top_of_deck":
            context.state.deck.draw_pile.insert(0, removed_card_id)
            destination = "top_of_deck"
        elif action == "remove_one_propaganda_card_from_game":
            context.state.deck.remove_from_game(removed_card_id, event_bus=context.event_bus, reason=f"journalist_{action}", player_id=journalist_player_id)
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
            drawn = context.state.deck.draw_cards(
                1,
                config=context.config.deck,
                rng=context.state.rng,
                event_bus=context.event_bus,
                reason="media_mogul_draw",
                player_id=context.state.round.media_mogul_player_id,
                destination="media_mogul_choices",
                game_id=context.config.game_id,
                round_number=context.state.round.round_number,
                phase=context.phase_name,
            )
            if not drawn:
                break
            card_id = drawn[0]
            inspected_cards.append(card_id)
            if card_id in self.cards_by_id and self.cards_by_id[card_id].type in allowed_types:
                drawn_cards.append(card_id)
            else:
                context.state.deck.discard_card(card_id, event_bus=context.event_bus, reason="media_mogul_ineligible", player_id=context.state.round.media_mogul_player_id)
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
                context.state.deck.discard_card(card_id, event_bus=context.event_bus, reason="action_resolved", player_id=revealed_action.player_id)
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
        if self._is_v03_rules():
            self._check_v03_victory(context)
            return
        self._check_victory("end_of_round")
        self.effect_engine.trigger("on_round_end", state=context.state)

    def _phase_research_assignments(self, context: PhaseContext) -> None:
        if self.ended_by is not None:
            return
        max_assignments = int(context.config.v03.get("research_assignments", {}).get("max_assignments", 2)) if self._is_v03_rules() else 2
        completed_count = 0
        discarded_count = 0
        checked_count = 0
        refilled_count = 0
        for player_id in self._ordered_player_ids_from_start():
            player = context.state.players[player_id]
            player_completed = 0
            if player.hidden_research_orders:
                bot_context = self.legal_actions.build_context(context.state, player_id)
                decision = self.bots[player_id].choose_completed_research_assignment_to_score(
                    bot_context,
                    list(player.hidden_research_orders),
                    context.state.rng,
                )
                selected = str(decision.choice) if decision.choice in player.hidden_research_orders else player.hidden_research_orders[0]
                checked_count += 1
                if self._research_order_is_fulfilled(selected, context):
                    player.hidden_research_orders.remove(selected)
                    context.state.deck.discard_card(selected, event_bus=context.event_bus, reason="v03_research_order_completed", player_id=player_id)
                    research_card = self.cards_by_id.get(selected)
                    configured_reward = int(
                        research_card.points
                        if research_card is not None and research_card.points is not None
                        else context.config.v03.get("research_assignments", {}).get("source_reward", 1)
                    )
                    source_reward = min(configured_reward, max(0, player.max_sources - len(player.sources)))
                    for _ in range(source_reward):
                        player.sources.append(f"source_token_{context.state.round.round_number}_{player_id}_{len(player.sources) + 1}")
                    completed_count += 1
                    player_completed += 1
                    context.event_bus.emit(
                        EventType.RESEARCH_ORDER_COMPLETED,
                        {
                            "player_id": player_id,
                            "card_id": selected,
                            "card_points": configured_reward,
                            "source_reward": source_reward,
                            "source_limit": player.max_sources,
                            "sources_after": len(player.sources),
                            "reason": decision.reason,
                        },
                    )
                else:
                    if player_completed >= 1:
                        continue
                    redraw_decision = self.bots[player_id].choose_whether_to_discard_research_assignment(
                        bot_context,
                        [True, False],
                        context.state.rng,
                    )
                    if redraw_decision.choice is not True:
                        continue
                    discard_decision = self.bots[player_id].choose_research_assignment_to_discard(
                        bot_context,
                        list(player.hidden_research_orders),
                        context.state.rng,
                    )
                    selected = str(discard_decision.choice) if discard_decision.choice in player.hidden_research_orders else selected
                    player.hidden_research_orders.remove(selected)
                    context.state.deck.discard_card(selected, event_bus=context.event_bus, reason="v03_research_order_redraw", player_id=player_id)
                    discarded_count += 1
                    context.event_bus.emit(
                        EventType.RESEARCH_ORDER_DISCARDED,
                        {
                            "player_id": player_id,
                            "card_id": selected,
                            "redraw_reason": redraw_decision.reason,
                            "discard_reason": discard_decision.reason,
                            "reason": "v03_no_completed_assignment_discard_and_redraw",
                        },
                    )
            while len(player.hidden_research_orders) < max_assignments and context.state.deck.research_order_pool:
                drawn = context.state.deck.research_order_pool.pop(0)
                player.hidden_research_orders.append(drawn)
                refilled_count += 1
                context.event_bus.emit(
                    EventType.CARD_DRAWN,
                    {
                        "player_id": player_id,
                        "card_id": context.state.deck.logical_card_id(drawn),
                        "instance_id": drawn,
                        "destination": "hidden_research_orders",
                        "reason": "v03_research_assignment_refill",
                    },
                )
        context.event_bus.emit(
            EventType.RESEARCH_ASSIGNMENTS_CHECKED,
            {
                "checked_count": checked_count,
                "completed_count": completed_count,
                "discarded_count": discarded_count,
                "refilled_count": refilled_count,
                "max_assignments": max_assignments,
            },
        )

    def _phase_round_end(self, context: PhaseContext) -> None:
        if self.ended_by is not None:
            return
        moved_cards = list(context.state.world_history_row)
        for card_id in moved_cards:
            context.state.deck.discard_card(card_id, event_bus=context.event_bus, reason="v03_world_history_row_cleanup")
        context.state.world_history_row.clear()
        ordered = self._ordered_player_ids("clockwise")
        current_index = ordered.index(context.state.round.start_player_id)
        context.state.round.start_player_id = ordered[(current_index + 1) % len(ordered)]
        self.effect_engine.trigger("on_round_end", state=context.state)
        context.event_bus.emit(
            EventType.ROUND_ENDED,
            {
                "discarded_world_history_card_ids": moved_cards,
                "next_start_player_id": context.state.round.start_player_id,
                "round": context.state.round.round_number,
            },
        )

    def _ordered_player_ids(self, direction: str) -> list[str]:
        ordered = [player.id for player in sorted(self.config.players, key=lambda player: player.seat)]
        if direction == "counterclockwise":
            return list(reversed(ordered))
        return ordered

    def _is_v03_rules(self) -> bool:
        return bool(self.config.v03) or "world_history_and_combat" in self.config.round_flow.phases

    def _ordered_player_ids_from_start(self) -> list[str]:
        ordered = self._ordered_player_ids("clockwise")
        start_index = ordered.index(self.state.round.start_player_id)
        return ordered[start_index:] + ordered[:start_index]

    def _tiebreak_players(self, candidates: list[str]) -> str:
        ordered = self._ordered_player_ids_from_start()
        for player_id in ordered:
            if player_id in candidates:
                return player_id
        return candidates[0]

    def _world_history_base_power(self, row: list[str]) -> dict[str, int]:
        power = {faction_id: 0 for faction_id in self.state.factions}
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is None or card.faction not in power:
                continue
            power[card.faction] += max(0, int(card.strength))
        return power

    def _active_propaganda_power(self, base_power: dict[str, int]) -> dict[str, int]:
        slot_factors = self.config.v03.get("propaganda", {}).get("slot_factors", [1 for _ in self.state.propaganda_track.get_slots()])
        power = {faction_id: 0 for faction_id in self.state.factions}
        for index, card_id in enumerate(self.state.propaganda_track.get_slots()):
            if card_id is None:
                continue
            card = self.cards_by_id.get(card_id)
            if card is None or card.faction not in power:
                continue
            if base_power.get(card.faction, 0) <= 0:
                continue
            factor = int(slot_factors[index]) if index < len(slot_factors) else 1
            power[card.faction] += max(0, int(card.strength)) * factor
        return power

    def _apply_v03_power_text_effects(
        self,
        row: list[str],
        base_power: dict[str, int],
        propaganda_power: dict[str, int],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        modifiers: list[dict[str, Any]] = []
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is None or not card.effect_text:
                continue
            modifiers.extend(
                self._apply_v03_single_power_effect(
                    card_id,
                    card,
                    source="world_history",
                    slot_index=None,
                    row=row,
                    base_power=base_power,
                    propaganda_power=propaganda_power,
                    total_power=total_power,
                    context=context,
                )
            )
        for slot_index, card_id in enumerate(context.state.propaganda_track.get_slots()):
            card = self.cards_by_id.get(card_id or "")
            if card is None or not card.effect_text:
                continue
            modifiers.extend(
                self._apply_v03_single_power_effect(
                    str(card_id),
                    card,
                    source="propaganda",
                    slot_index=slot_index,
                    row=row,
                    base_power=base_power,
                    propaganda_power=propaganda_power,
                    total_power=total_power,
                    context=context,
                )
            )
        return modifiers

    def _apply_v03_single_power_effect(
        self,
        instance_id: str,
        card: CardConfig,
        *,
        source: Literal["world_history", "propaganda"],
        slot_index: int | None,
        row: list[str],
        base_power: dict[str, int],
        propaganda_power: dict[str, int],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        text = self._normalize_condition(card.effect_text or "")
        if source == "world_history" and "solange diese karte als propaganda ausliegt" in text:
            return []
        if source == "propaganda" and not self._v03_slot_condition_matches(text, slot_index):
            return []
        if "macht" not in text and "propagandamacht" not in text:
            return []
        if not self._v03_power_condition_matches(text, card, row, base_power, propaganda_power, total_power, context):
            return []

        modifiers: list[dict[str, Any]] = []
        faction_name_pattern = "|".join(self._v03_faction_name_map())
        for match in re.finditer(rf"\b({faction_name_pattern})\s+erhaelt\s+\+(\d+)\s+macht\b", text):
            faction_id = self._v03_faction_name_map()[match.group(1)]
            amount = int(match.group(2))
            modifiers.append(self._apply_v03_power_delta(instance_id, faction_id, amount, "text_add_power", total_power, context))
        for match in re.finditer(rf"\berhaelt\s+({faction_name_pattern})\s+\+(\d+)\s+macht\b", text):
            faction_id = self._v03_faction_name_map()[match.group(1)]
            amount = int(match.group(2))
            modifiers.append(self._apply_v03_power_delta(instance_id, faction_id, amount, "text_add_power", total_power, context))

        variable_modifier = self._v03_variable_power_modifier(instance_id, card, text, row, total_power, context)
        if variable_modifier is not None:
            modifiers.append(variable_modifier)

        reduction = self._v03_power_reduction_target(text, total_power, context)
        if reduction is not None:
            faction_id, amount = reduction
            modifiers.append(self._apply_v03_power_delta(instance_id, faction_id, -amount, "text_reduce_power", total_power, context))
        return modifiers

    def _apply_v03_power_delta(
        self,
        card_id: str,
        faction_id: str,
        amount: int,
        reason: str,
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> dict[str, Any]:
        before = int(total_power.get(faction_id, 0))
        total_power[faction_id] = before + amount
        payload = {
            "card_id": card_id,
            "effect_type": "v03_power_modifier",
            "reason": reason,
            "target_faction_id": faction_id,
            "amount": amount,
            "power_before": before,
            "power_after": total_power[faction_id],
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _apply_v03_propaganda_text_effects(
        self,
        row: list[str],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        sources: list[tuple[str, CardConfig, int | None, Literal["world_history", "propaganda"]]] = []
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is not None:
                sources.append((card_id, card, None, "world_history"))
        for slot_index, card_id in enumerate(context.state.propaganda_track.get_slots()):
            card = self.cards_by_id.get(card_id or "")
            if card is not None:
                sources.append((str(card_id), card, slot_index, "propaganda"))

        for instance_id, card, slot_index, source in sources:
            text = self._normalize_condition(card.effect_text or "")
            if source == "world_history" and "solange diese karte als propaganda ausliegt" in text:
                continue
            if source == "propaganda" and not self._v03_slot_condition_matches(text, slot_index):
                continue
            if not any(marker in text for marker in ["entferne", "verschiebe", "tausche"]):
                continue
            if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
                continue
            effect = self._v03_apply_single_propaganda_effect(instance_id, card, text, total_power, context)
            if effect is not None:
                effects.append(effect)
        return effects

    def _v03_apply_single_propaganda_effect(
        self,
        instance_id: str,
        card: CardConfig,
        text: str,
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        if "tausche zwei benachbarte propagandakarten" in text:
            return self._v03_swap_adjacent_propaganda(instance_id, context)
        if "verschiebe die propagandakarte aus slot 1 in slot 4" in text:
            return self._v03_move_slot_one_to_slot_four(instance_id, context)
        move_faction = self._v03_move_back_faction(text)
        if move_faction is not None:
            return self._v03_move_faction_propaganda_back(instance_id, move_faction, context)
        target = self._v03_propaganda_removal_target(text, card, total_power, context)
        if target is not None:
            removed_effect = self._v03_remove_propaganda_card(instance_id, target, "text_remove_propaganda", context)
            if (
                removed_effect is not None
                and "wenn danach keine propagandakarte dieser fraktion mehr ausliegt" in text
                and not any(self._card_faction(card_id) == removed_effect.get("removed_faction_id") for card_id in context.state.propaganda_track.get_slots())
            ):
                self._v03_apply_neutral_destruction(instance_id, 1, "text_remove_last_propaganda_destroy_neutral", context)
            return removed_effect
        return None

    def _v03_propaganda_removal_target(
        self,
        text: str,
        card: CardConfig,
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> str | None:
        slots = context.state.propaganda_track.get_slots()
        occupied = [card_id for card_id in slots if card_id is not None]
        if not occupied or "entferne" not in text:
            return None
        if "propagandakarte aus slot 1" in text:
            return slots[0] if slots else None
        if "propagandakarte mit dem hoechsten einzelnen propagandamachtwert" in text:
            return self._v03_highest_single_propaganda_card(slots)
        if "propagandakarte der fraktion mit der hoechsten propagandamacht" in text:
            powers = self._objective_propaganda_power(slots)
            faction_id = self._power_tiebreak(max(powers.values()), powers)
            return self._v03_oldest_propaganda_for_faction(faction_id, slots)
        if "propagandakarte der fraktion mit der hoechsten macht" in text:
            faction_id = self._power_tiebreak(max(total_power.values()), total_power)
            return self._v03_oldest_propaganda_for_faction(faction_id, slots)
        for faction_name, faction_id in self._v03_faction_name_map().items():
            if f"entferne 1 {self._v03_faction_adjective(faction_id)} propagandakarte" in text or f"entferne eine {self._v03_faction_adjective(faction_id)} propagandakarte" in text:
                return self._v03_oldest_propaganda_for_faction(faction_id, slots)
        if "entferne 1 propagandakarte" in text:
            return occupied[0]
        return None

    def _v03_remove_propaganda_card(
        self,
        source_card_id: str,
        target_card_id: str,
        reason: str,
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        removed = context.state.propaganda_track.remove_card(target_card_id)
        if removed is None:
            return None
        removed_faction = self._card_faction(removed)
        context.state.deck.discard_card(removed, event_bus=context.event_bus, reason=reason)
        payload = {
            "card_id": source_card_id,
            "effect_type": "v03_propaganda_removed",
            "reason": reason,
            "removed_card_id": removed,
            "removed_faction_id": removed_faction,
            "slots": context.state.propaganda_track.get_slots(),
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        context.event_bus.emit(
            EventType.PROPAGANDA_REMOVED,
            {
                "card_id": removed,
                "source_card_id": source_card_id,
                "reason": reason,
                "slots": context.state.propaganda_track.get_slots(),
            },
        )
        return payload

    def _v03_swap_adjacent_propaganda(self, source_card_id: str, context: PhaseContext) -> dict[str, Any] | None:
        slots = context.state.propaganda_track.slots
        for index in range(len(slots) - 1):
            if slots[index] is not None and slots[index + 1] is not None:
                before = list(slots)
                slots[index], slots[index + 1] = slots[index + 1], slots[index]
                return self._v03_emit_propaganda_moved(source_card_id, "text_swap_adjacent_propaganda", before, context)
        return None

    def _v03_move_slot_one_to_slot_four(self, source_card_id: str, context: PhaseContext) -> dict[str, Any] | None:
        slots = context.state.propaganda_track.slots
        if not slots or slots[0] is None:
            return None
        before = list(slots)
        card_id = slots.pop(0)
        slots.append(card_id)
        return self._v03_emit_propaganda_moved(source_card_id, "text_move_slot_1_to_slot_4", before, context)

    def _v03_move_faction_propaganda_back(
        self,
        source_card_id: str,
        faction_id: str,
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        slots = context.state.propaganda_track.slots
        for index, card_id in enumerate(slots[:-1]):
            if self._card_faction(card_id) != faction_id or slots[index + 1] is not None:
                continue
            before = list(slots)
            slots[index], slots[index + 1] = slots[index + 1], slots[index]
            return self._v03_emit_propaganda_moved(source_card_id, "text_move_faction_propaganda_back", before, context)
        for index, card_id in enumerate(slots[:-1]):
            if self._card_faction(card_id) == faction_id:
                before = list(slots)
                slots[index], slots[index + 1] = slots[index + 1], slots[index]
                return self._v03_emit_propaganda_moved(source_card_id, "text_move_faction_propaganda_back", before, context)
        return None

    def _v03_emit_propaganda_moved(
        self,
        source_card_id: str,
        reason: str,
        before: list[str | None],
        context: PhaseContext,
    ) -> dict[str, Any]:
        payload = {
            "card_id": source_card_id,
            "effect_type": "v03_propaganda_moved",
            "reason": reason,
            "slots_before": before,
            "slots_after": context.state.propaganda_track.get_slots(),
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _v03_move_back_faction(self, text: str) -> str | None:
        for faction_id in self.state.factions:
            if f"verschiebe eine {self._v03_faction_adjective(faction_id)} propagandakarte einen slot nach hinten" in text:
                return faction_id
        return None

    def _v03_oldest_propaganda_for_faction(self, faction_id: str, slots: list[str | None]) -> str | None:
        for card_id in slots:
            if self._card_faction(card_id) == faction_id:
                return card_id
        return None

    def _v03_highest_single_propaganda_card(self, slots: list[str | None]) -> str | None:
        candidates: list[tuple[int, int, str]] = []
        slot_factors = self.config.v03.get("propaganda", {}).get("slot_factors", [1 for _ in slots])
        for index, card_id in enumerate(slots):
            card = self.cards_by_id.get(card_id or "")
            if card is None:
                continue
            factor = int(slot_factors[index]) if index < len(slot_factors) else 1
            candidates.append((max(0, int(card.strength)) * factor, -index, str(card_id)))
        if not candidates:
            return None
        return max(candidates)[2]

    def _apply_v03_displaced_propaganda_effect(self, card_id: str, context: PhaseContext) -> dict[str, Any] | None:
        card = self.cards_by_id.get(card_id)
        if card is None or not card.effect_text:
            return None
        text = self._normalize_condition(card.effect_text)
        if "wenn diese karte verdraengt wird" not in text:
            return None
        if "rekrutiert" in text and card.faction in context.state.factions:
            amount = self._v03_recruit_amount(text, card, context) or 0
            if amount > 0:
                return self._v03_apply_population_delta(card_id, card.faction, amount, "v03_displaced_recruit", context)
        if "stelle" in text:
            controls = {"prevent_first_restore": False, "restore_prevented_used": False, "restore_limit": None, "restore_used": 0}
            return self._v03_apply_restoration(card_id, self._v03_restore_amount(text), controls, context)
        destroyed_neutral = self._v03_destroy_neutral_amount(text, context)
        if destroyed_neutral > 0:
            return self._v03_apply_neutral_destruction(card_id, destroyed_neutral, "v03_displaced_destroy_neutral", context)
        return None

    def _apply_v03_utility_text_effects(
        self,
        row: list[str],
        total_power: dict[str, int],
        attack_records: list[dict[str, Any]],
        applied_deltas: dict[str, int],
        propaganda_effects: list[dict[str, Any]],
        population_effects: list[dict[str, Any]],
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        sources: list[tuple[str, CardConfig, int | None, Literal["world_history", "propaganda"]]] = []
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is not None:
                sources.append((card_id, card, None, "world_history"))
        for slot_index, card_id in enumerate(context.state.propaganda_track.get_slots()):
            card = self.cards_by_id.get(card_id or "")
            if card is not None:
                sources.append((str(card_id), card, slot_index, "propaganda"))
        utility_controls: dict[str, Any] = {
            "draw_limit_per_player": None,
            "drawn_by_player": {},
        }
        for instance_id, card, slot_index, source in sources:
            text = self._normalize_condition(card.effect_text or "")
            if source == "world_history" and "solange diese karte als propaganda ausliegt" in text:
                continue
            if source == "propaganda" and not self._v03_slot_condition_matches(text, slot_index):
                continue
            if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
                continue
            draw_limit_match = re.search(r"kein spieler kann .* mehr als\s+(\d+)\s+karte ziehen", text)
            if draw_limit_match:
                current = utility_controls["draw_limit_per_player"]
                amount = int(draw_limit_match.group(1))
                utility_controls["draw_limit_per_player"] = amount if current is None else min(int(current), amount)
                context.event_bus.emit(
                    EventType.EFFECT_TRIGGERED,
                    {
                        "card_id": instance_id,
                        "effect_type": "v03_utility_control",
                        "reason": "draw_limit_per_player",
                        "amount": amount,
                    },
                )
        for instance_id, card, slot_index, source in sources:
            text = self._normalize_condition(card.effect_text or "")
            if source == "world_history" and "solange diese karte als propaganda ausliegt" in text:
                continue
            if source == "propaganda" and not self._v03_slot_condition_matches(text, slot_index):
                continue
            if not any(marker in text for marker in ["zieht", "wirft", "quelle", "rechercheauftrag", "nachziehstapel"]):
                continue
            if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
                continue
            effect = self._v03_apply_single_utility_effect(
                instance_id,
                card,
                text,
                total_power,
                attack_records,
                applied_deltas,
                propaganda_effects,
                population_effects,
                utility_controls,
                context,
            )
            if effect is not None:
                effects.append(effect)
        return effects

    def _v03_apply_single_utility_effect(
        self,
        instance_id: str,
        card: CardConfig,
        text: str,
        total_power: dict[str, int],
        attack_records: list[dict[str, Any]],
        applied_deltas: dict[str, int],
        propaganda_effects: list[dict[str, Any]],
        population_effects: list[dict[str, Any]],
        utility_controls: dict[str, Any],
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        if "spieler mit den wenigsten handkarten" in text and "zieht" in text:
            if "erfolgreichen angriff" in text and not self._v03_has_successful_attack(card.faction, attack_records):
                return None
            if "rekrutiert" in text and card.faction not in {
                effect.get("target_faction_id")
                for effect in population_effects
                if effect.get("reason") == "text_recruit" and int(effect.get("applied_delta", 0)) > 0
            }:
                return None
            player_id = self._v03_player_with_fewest_handcards(context)
            return self._v03_draw_to_player(instance_id, player_id, 1, "text_draw_fewest_handcards", context, utility_controls)
        if "medienmogul zieht" in text:
            draw_match = re.search(r"medienmogul zieht\s+(\d+)\s+karte", text)
            amount = int(draw_match.group(1)) if draw_match else 1
            player_id = context.state.round.media_mogul_player_id
            draw_effect = self._v03_draw_to_player(instance_id, player_id, amount, "text_media_mogul_draw", context, utility_controls)
            if "wirft" in text:
                discard_effect = self._v03_discard_player_hand_card(instance_id, player_id, "text_media_mogul_discard", context)
                if discard_effect is not None:
                    draw_effect["discard_effect"] = discard_effect
            return draw_effect
        if "medienmogul wirft 1 handkarte ab" in text:
            discard_effect = self._v03_discard_player_hand_card(instance_id, context.state.round.media_mogul_player_id, "text_media_mogul_discard", context)
            return discard_effect
        if "journalist" in text and "erhaelt" in text and "quelle" in text:
            if "propagandakarte entfernt" in text and not any(effect.get("effect_type") == "v03_propaganda_removed" for effect in propaganda_effects):
                return None
            if "propagandakarte verschoben" in text and not any(effect.get("effect_type") == "v03_propaganda_moved" for effect in propaganda_effects):
                return None
            if "fraktion mit hoeherer propagandamacht angreift" in text and not self._v03_attack_higher_propaganda_power(card.faction, attack_records, context):
                return None
            return self._v03_give_source_to_player(instance_id, context.state.round.journalist_player_id, "text_journalist_gain_source", context)
        if "journalist" in text and "zieht" in text and "rechercheauftrag" in text:
            return self._v03_draw_research_order_to_player(instance_id, context.state.round.journalist_player_id, "text_journalist_draw_research_order", context)
        if "sieht die obersten" in text and "nachziehstapel" in text:
            player_id = context.state.round.journalist_player_id if "journalist" in text else context.state.round.start_player_id
            return self._v03_peek_and_reorder_deck(instance_id, player_id, text, context)
        return None

    def _v03_has_successful_attack(self, faction_id: str | None, attack_records: list[dict[str, Any]]) -> bool:
        return faction_id is not None and any(record["attacker"] == faction_id and int(record.get("impact", 0)) > 0 for record in attack_records)

    def _v03_player_with_fewest_handcards(self, context: PhaseContext) -> str:
        hand_counts = {player_id: len(player.hand) for player_id, player in context.state.players.items()}
        return self._value_tiebreak(min(hand_counts.values()), hand_counts)

    def _v03_draw_to_player(
        self,
        card_id: str,
        player_id: str,
        amount: int,
        reason: str,
        context: PhaseContext,
        utility_controls: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested_amount = amount
        if utility_controls is not None and utility_controls.get("draw_limit_per_player") is not None:
            already_drawn = int(utility_controls["drawn_by_player"].get(player_id, 0))
            remaining = max(0, int(utility_controls["draw_limit_per_player"]) - already_drawn)
            amount = min(amount, remaining)
        drawn = context.state.deck.draw_cards(
            amount,
            config=context.config.deck,
            rng=context.state.rng,
            event_bus=context.event_bus,
            reason=reason,
            player_id=player_id,
            destination="player_hand",
            game_id=context.config.game_id,
            round_number=context.state.round.round_number,
            phase=context.phase_name,
        )
        context.state.players[player_id].hand.extend(drawn)
        if utility_controls is not None:
            utility_controls["drawn_by_player"][player_id] = int(utility_controls["drawn_by_player"].get(player_id, 0)) + len(drawn)
        payload = {
            "card_id": card_id,
            "effect_type": "v03_utility_effect",
            "reason": reason,
            "player_id": player_id,
            "drawn_card_ids": drawn,
            "requested_count": requested_amount,
            "applied_count": len(drawn),
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _v03_discard_player_hand_card(
        self,
        card_id: str,
        player_id: str,
        reason: str,
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        player = context.state.players[player_id]
        if not player.hand:
            payload = {
                "card_id": card_id,
                "effect_type": "v03_utility_effect",
                "reason": reason,
                "player_id": player_id,
                "discarded_card_id": None,
                "applied_count": 0,
            }
            context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
            return payload
        discarded = player.hand.pop(0)
        context.state.deck.discard_card(discarded, event_bus=context.event_bus, reason=reason, player_id=player_id)
        payload = {
            "card_id": card_id,
            "effect_type": "v03_utility_effect",
            "reason": reason,
            "player_id": player_id,
            "discarded_card_id": discarded,
            "applied_count": 1,
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _v03_give_source_to_player(
        self,
        card_id: str,
        player_id: str,
        reason: str,
        context: PhaseContext,
    ) -> dict[str, Any]:
        player = context.state.players[player_id]
        before = len(player.sources)
        if len(player.sources) < player.max_sources:
            player.sources.append(f"source_token_{context.state.round.round_number}_{player_id}_{len(player.sources) + 1}")
        payload = {
            "card_id": card_id,
            "effect_type": "v03_utility_effect",
            "reason": reason,
            "player_id": player_id,
            "sources_before": before,
            "sources_after": len(player.sources),
            "applied_count": len(player.sources) - before,
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _v03_draw_research_order_to_player(
        self,
        card_id: str,
        player_id: str,
        reason: str,
        context: PhaseContext,
    ) -> dict[str, Any]:
        drawn: list[str] = []
        if context.state.deck.research_order_pool:
            drawn.append(context.state.deck.research_order_pool.pop(0))
        context.state.players[player_id].hidden_research_orders.extend(drawn)
        payload = {
            "card_id": card_id,
            "effect_type": "v03_utility_effect",
            "reason": reason,
            "player_id": player_id,
            "drawn_research_order_ids": drawn,
            "applied_count": len(drawn),
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _v03_peek_and_reorder_deck(
        self,
        card_id: str,
        player_id: str,
        text: str,
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        count_match = re.search(r"obersten\s+(\d+)\s+karten", text)
        count = int(count_match.group(1)) if count_match else 2
        if len(context.state.deck.draw_pile) < count:
            return None
        before = context.state.deck.draw_pile[:count]
        if "legt 1 oben" in text and "unter den nachziehstapel" in text and len(before) >= 2:
            top = before[0]
            bottom = before[1]
            context.state.deck.draw_pile = [top, *context.state.deck.draw_pile[count:], bottom]
        payload = {
            "card_id": card_id,
            "effect_type": "v03_utility_effect",
            "reason": "text_peek_and_reorder_deck",
            "player_id": player_id,
            "seen_count": count,
            "changed_order": before != context.state.deck.draw_pile[:count],
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _v03_attack_higher_propaganda_power(
        self,
        faction_id: str | None,
        attack_records: list[dict[str, Any]],
        context: PhaseContext,
    ) -> bool:
        if faction_id is None:
            return False
        prop_power = self._objective_propaganda_power(context.state.propaganda_track.get_slots())
        for record in attack_records:
            if record["attacker"] == faction_id and int(record.get("impact", 0)) > 0:
                if prop_power.get(str(record["defender"]), 0) > prop_power.get(faction_id, 0):
                    return True
        return False

    def _apply_v03_population_text_effects(
        self,
        row: list[str],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        controls = self._v03_population_controls(row, total_power, context)
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is None or not card.effect_text:
                continue
            effects.extend(
                self._apply_v03_single_population_effect(
                    card_id,
                    card,
                    source="world_history",
                    slot_index=None,
                    row=row,
                    total_power=total_power,
                    controls=controls,
                    context=context,
                )
            )
        for slot_index, card_id in enumerate(context.state.propaganda_track.get_slots()):
            card = self.cards_by_id.get(card_id or "")
            if card is None or not card.effect_text:
                continue
            effects.extend(
                self._apply_v03_single_population_effect(
                    str(card_id),
                    card,
                    source="propaganda",
                    slot_index=slot_index,
                    row=row,
                    total_power=total_power,
                    controls=controls,
                    context=context,
                )
            )
        return effects

    def _apply_v03_single_population_effect(
        self,
        instance_id: str,
        card: CardConfig,
        *,
        source: Literal["world_history", "propaganda"],
        slot_index: int | None,
        row: list[str],
        total_power: dict[str, int],
        controls: dict[str, Any],
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        text = self._normalize_condition(card.effect_text or "")
        if source == "world_history" and "solange diese karte als propaganda ausliegt" in text:
            return []
        if source == "propaganda" and not self._v03_slot_condition_matches(text, slot_index):
            return []
        if not any(marker in text for marker in ["rekrutiert", "neutralisiere", "vernichte", "stelle"]):
            return []
        if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
            return []

        effects: list[dict[str, Any]] = []
        recruit = self._v03_recruit_amount(text, card, context)
        if recruit is not None and card.faction in context.state.factions:
            adjusted_recruit, modifiers = self._v03_adjust_recruitment(instance_id, card.faction, recruit, controls, context)
            effects.extend(modifiers)
            if adjusted_recruit > 0:
                applied = self._v03_apply_population_delta(instance_id, card.faction, adjusted_recruit, "text_recruit", context)
                effects.append(applied)
                if applied["applied_delta"] > 0:
                    controls["recruited_factions"].add(card.faction)
                    recruited_by_faction = controls["recruited_amount_by_faction"]
                    recruited_by_faction[card.faction] = int(recruited_by_faction.get(card.faction, 0)) + int(applied["applied_delta"])
                    controls["recruit_events"] += 1
        neutral_targets = self._v03_neutralize_targets(text, card, total_power, context)
        for faction_id, amount in neutral_targets:
            effects.append(self._v03_apply_population_delta(instance_id, faction_id, -amount, "text_neutralize", context))
        destroyed_neutral = self._v03_destroy_neutral_amount(text, context)
        if destroyed_neutral > 0:
            effects.append(self._v03_apply_neutral_destruction(instance_id, destroyed_neutral, "text_destroy_neutral", context))
        restore = self._v03_restore_amount(text)
        if restore > 0:
            effects.append(self._v03_apply_restoration(instance_id, restore, controls, context))
        return [effect for effect in effects if effect]

    def _v03_population_controls(
        self,
        row: list[str],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> dict[str, Any]:
        controls: dict[str, Any] = {
            "blocked_recruit_factions": set(),
            "recruit_reduction_all": 0,
            "recruit_reduction_if_at_least_two": 0,
            "recruit_limit": None,
            "recruited_amount_by_faction": {},
            "extra_first_recruit_by_faction": {},
            "restore_limit": None,
            "prevent_first_restore": False,
            "restore_used": 0,
            "restore_prevented_used": False,
            "recruited_factions": set(),
            "recruit_events": 0,
            "control_events": [],
        }
        sources: list[tuple[str, CardConfig, int | None, Literal["world_history", "propaganda"]]] = []
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is not None:
                sources.append((card_id, card, None, "world_history"))
        for slot_index, card_id in enumerate(context.state.propaganda_track.get_slots()):
            card = self.cards_by_id.get(card_id or "")
            if card is not None:
                sources.append((str(card_id), card, slot_index, "propaganda"))

        for instance_id, card, slot_index, source in sources:
            text = self._normalize_condition(card.effect_text or "")
            if source == "world_history" and "solange diese karte als propaganda ausliegt" in text:
                continue
            if source == "propaganda" and not self._v03_slot_condition_matches(text, slot_index):
                continue
            if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
                continue
            control_events = self._v03_population_controls_from_text(instance_id, card, text, context)
            for event in control_events:
                reason = event["reason"]
                if reason == "block_recruit":
                    controls["blocked_recruit_factions"].update(event["faction_ids"])
                elif reason == "reduce_all_recruit":
                    controls["recruit_reduction_all"] += int(event["amount"])
                elif reason == "reduce_recruit_if_at_least_two":
                    controls["recruit_reduction_if_at_least_two"] += int(event["amount"])
                elif reason == "recruit_limit":
                    current = controls["recruit_limit"]
                    controls["recruit_limit"] = int(event["amount"]) if current is None else min(int(current), int(event["amount"]))
                elif reason == "extra_first_recruit":
                    extras = controls["extra_first_recruit_by_faction"]
                    extras[event["faction_id"]] = int(extras.get(event["faction_id"], 0)) + int(event["amount"])
                elif reason == "restore_limit":
                    current = controls["restore_limit"]
                    controls["restore_limit"] = int(event["amount"]) if current is None else min(int(current), int(event["amount"]))
                elif reason == "prevent_first_restore":
                    controls["prevent_first_restore"] = True
                controls["control_events"].append(event)
                context.event_bus.emit(EventType.EFFECT_TRIGGERED, event)
        return controls

    def _v03_population_controls_from_text(
        self,
        instance_id: str,
        card: CardConfig,
        text: str,
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if "kann ab jetzt bis zum ende dieser weltgeschichte nicht rekrutieren" in text or "kann in dieser weltgeschichte nicht rekrutieren" in text:
            blocked = self._v03_recruit_block_targets(text, card, context)
            if blocked:
                events.append(
                    {
                        "card_id": instance_id,
                        "effect_type": "v03_population_control",
                        "reason": "block_recruit",
                        "faction_ids": blocked,
                    }
                )
        if "jede fraktion, die in dieser weltgeschichte rekrutieren wuerde, rekrutiert 1 bevoelkerung weniger" in text:
            events.append(
                {
                    "card_id": instance_id,
                    "effect_type": "v03_population_control",
                    "reason": "reduce_all_recruit",
                    "amount": 1,
                }
            )
        if "2 oder mehr bevoelkerung rekrutieren wuerde, rekrutiert sie 1 bevoelkerung weniger" in text:
            events.append(
                {
                    "card_id": instance_id,
                    "effect_type": "v03_population_control",
                    "reason": "reduce_recruit_if_at_least_two",
                    "amount": 1,
                }
            )
        recruit_limit_match = re.search(r"kann keine fraktion mehr als\s+(\d+)\s+bevoelkerung pro weltgeschichte rekrutieren", text)
        if recruit_limit_match:
            events.append(
                {
                    "card_id": instance_id,
                    "effect_type": "v03_population_control",
                    "reason": "recruit_limit",
                    "amount": int(recruit_limit_match.group(1)),
                }
            )
        for faction_id in context.state.factions:
            faction_name = self._v03_faction_display_name(faction_id)
            if f"wenn {faction_name} rekrutiert, rekrutiert {faction_name} 1 zusaetzliche bevoelkerung" in text:
                events.append(
                    {
                        "card_id": instance_id,
                        "effect_type": "v03_population_control",
                        "reason": "extra_first_recruit",
                        "faction_id": faction_id,
                        "amount": 1,
                    }
                )
        restore_limit_match = re.search(r"kann hoechstens\s+(\d+)\s+bevoelkerung wiederhergestellt werden", text)
        if restore_limit_match:
            events.append(
                {
                    "card_id": instance_id,
                    "effect_type": "v03_population_control",
                    "reason": "restore_limit",
                    "amount": int(restore_limit_match.group(1)),
                }
            )
        if "wiederhergestellt wuerde, wird diese wiederherstellung verhindert" in text:
            events.append(
                {
                    "card_id": instance_id,
                    "effect_type": "v03_population_control",
                    "reason": "prevent_first_restore",
                }
            )
        return events

    def _v03_recruit_block_targets(self, text: str, card: CardConfig, context: PhaseContext) -> list[str]:
        blocked: set[str] = set()
        for faction_name, faction_id in self._v03_faction_name_map().items():
            if f"{faction_name} kann" in text:
                blocked.add(faction_id)
        if "diese fraktion kann" in text or "fraktion mit der meisten bevoelkerung kann" in text:
            blocked.add(self._population_tiebreak(max, context))
        if "fraktion mit der wenigsten bevoelkerung" in text and "kann" in text:
            blocked.add(self._population_tiebreak(min, context))
        if "jede fraktion mit mindestens 12 bevoelkerung" in text:
            blocked.update(
                faction_id
                for faction_id, faction in context.state.factions.items()
                if faction.population >= 12
            )
        if card.faction in context.state.factions and "kann in dieser weltgeschichte nicht rekrutieren" in text:
            blocked.add(card.faction)
        return sorted(blocked)

    def _v03_adjust_recruitment(
        self,
        card_id: str,
        faction_id: str,
        requested: int,
        controls: dict[str, Any],
        context: PhaseContext,
    ) -> tuple[int, list[dict[str, Any]]]:
        amount = requested
        modifiers: list[dict[str, Any]] = []
        if faction_id in controls["blocked_recruit_factions"]:
            modifiers.append(self._v03_emit_population_adjustment(card_id, faction_id, "recruit_blocked", -amount, requested, 0, context))
            return 0, modifiers
        extras = controls["extra_first_recruit_by_faction"]
        if faction_id in extras and faction_id not in controls["recruited_factions"]:
            extra = int(extras[faction_id])
            before = amount
            amount += extra
            modifiers.append(self._v03_emit_population_adjustment(card_id, faction_id, "extra_first_recruit", extra, before, amount, context))
        if amount >= 2 and controls["recruit_reduction_if_at_least_two"]:
            reduction = min(amount, int(controls["recruit_reduction_if_at_least_two"]))
            before = amount
            amount -= reduction
            modifiers.append(self._v03_emit_population_adjustment(card_id, faction_id, "reduce_recruit_if_at_least_two", -reduction, before, amount, context))
        if controls["recruit_reduction_all"]:
            reduction = min(amount, int(controls["recruit_reduction_all"]))
            before = amount
            amount -= reduction
            modifiers.append(self._v03_emit_population_adjustment(card_id, faction_id, "reduce_all_recruit", -reduction, before, amount, context))
        if controls["recruit_limit"] is not None:
            already_recruited = int(controls["recruited_amount_by_faction"].get(faction_id, 0))
            remaining = max(0, int(controls["recruit_limit"]) - already_recruited)
            if amount > remaining:
                before = amount
                amount = remaining
                modifiers.append(self._v03_emit_population_adjustment(card_id, faction_id, "recruit_limit", amount - before, before, amount, context))
        return max(0, amount), modifiers

    def _v03_emit_population_adjustment(
        self,
        card_id: str,
        faction_id: str,
        reason: str,
        amount: int,
        before: int,
        after: int,
        context: PhaseContext,
    ) -> dict[str, Any]:
        payload = {
            "card_id": card_id,
            "effect_type": "v03_population_adjustment",
            "reason": reason,
            "target_faction_id": faction_id,
            "amount": amount,
            "before": before,
            "after": after,
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        return payload

    def _v03_recruit_amount(self, text: str, card: CardConfig, context: PhaseContext) -> int | None:
        faction_id = card.faction
        if faction_id not in context.state.factions:
            return None
        faction_name = self._v03_faction_display_name(faction_id)
        match = re.search(rf"{faction_name}\s+rekrutiert\s+(?:bis\s+zu\s+)?(\d+)\s+bevoelkerung", text)
        if match is None:
            match = re.search(rf"rekrutiert\s+{faction_name}\s+(?:bis\s+zu\s+)?(\d+)\s+bevoelkerung", text)
        if match is None:
            match = re.search(r"rekrutiert\s+(?:bis\s+zu\s+)?(\d+)\s+bevoelkerung", text)
        if match is None:
            return None
        amount = int(match.group(1))
        prop_counts = self._research_propaganda_counts(context.state.propaganda_track.get_slots())
        if "stattdessen 2 bevoelkerung" in text and context.state.factions[faction_id].population == min(
            faction.population for faction in context.state.factions.values()
        ):
            amount = 2
        if "stattdessen 2 bevoelkerung" in text and prop_counts.get(faction_id, 0) >= 1:
            amount = 2
        return amount

    def _v03_neutralize_targets(
        self,
        text: str,
        card: CardConfig,
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> list[tuple[str, int]]:
        targets: list[tuple[str, int]] = []
        own_faction = card.faction
        for faction_name, faction_id in self._v03_faction_name_map().items():
            match = re.search(rf"neutralisiere\s+(\d+)\s+{self._v03_faction_adjective(faction_id)}\s+bevoelkerung", text)
            if match:
                targets.append((faction_id, int(match.group(1))))
        match = re.search(r"neutralisiere\s+(\d+)\s+bevoelkerung\s+der\s+fraktion\s+mit\s+der\s+meisten\s+bevoelkerung", text)
        if match:
            targets.append((self._population_tiebreak(max, context), int(match.group(1))))
        match = re.search(r"neutralisiere\s+(\d+)\s+bevoelkerung\s+der\s+fraktion\s+mit\s+der\s+wenigsten\s+bevoelkerung", text)
        if match:
            targets.append((self._population_tiebreak(min, context), int(match.group(1))))
        match = re.search(r"neutralisiere\s+(\d+)\s+bevoelkerung\s+der\s+fraktion\s+mit\s+der\s+hoechsten\s+macht", text)
        if match:
            targets.append((self._power_tiebreak(max(total_power.values()), total_power), int(match.group(1))))
        match = re.search(r"neutralisiere\s+(\d+)\s+bevoelkerung\s+jeder\s+fraktion,\s+die\s+mehr\s+bevoelkerung\s+als\s+([a-z]+)\s+hat", text)
        if match and own_faction in context.state.factions:
            amount = int(match.group(1))
            own_population = context.state.factions[own_faction].population
            for faction_id, faction in context.state.factions.items():
                if faction.population > own_population:
                    targets.append((faction_id, amount))
        match = re.search(r"neutralisiere\s+(\d+)\s+bevoelkerung\s+jeder\s+fraktion\s+mit\s+mindestens\s+(\d+)\s+bevoelkerung", text)
        if match:
            amount = int(match.group(1))
            threshold = int(match.group(2))
            for faction_id, faction in context.state.factions.items():
                if faction.population >= threshold:
                    targets.append((faction_id, amount))
        match = re.search(r"neutralisiere\s+(\d+)\s+bevoelkerung\s+jeder\s+nicht-[a-z]+\s+fraktion\s+mit\s+(\d+)\s+oder\s+weniger\s+bevoelkerung", text)
        if match and own_faction is not None:
            amount = int(match.group(1))
            threshold = int(match.group(2))
            for faction_id, faction in context.state.factions.items():
                if faction_id != own_faction and faction.population <= threshold:
                    targets.append((faction_id, amount))
        return targets

    def _v03_destroy_neutral_amount(self, text: str, context: PhaseContext) -> int:
        match = re.search(r"vernichte\s+(?:bis\s+zu\s+)?(\d+)\s+neutrale\s+bevoelkerung", text)
        if match:
            return int(match.group(1))
        return 0

    def _v03_restore_amount(self, text: str) -> int:
        match = re.search(r"stelle\s+(?:am ende dieser weltgeschichte\s+)?(\d+)\s+bevoelkerung\s+wieder her", text)
        if match:
            return int(match.group(1))
        return 0

    def _v03_apply_population_delta(
        self,
        card_id: str,
        faction_id: str,
        amount: int,
        reason: str,
        context: PhaseContext,
    ) -> dict[str, Any]:
        faction = context.state.factions[faction_id]
        before = faction.population
        neutral_before = context.state.neutral_population
        if amount > 0:
            applied = min(amount, context.state.neutral_population)
            faction.population += applied
            context.state.neutral_population -= applied
        else:
            applied = -min(before, abs(amount))
            faction.population += applied
            context.state.neutral_population -= applied
        payload = {
            "card_id": card_id,
            "effect_type": "v03_population_modifier",
            "reason": reason,
            "target_faction_id": faction_id,
            "requested_delta": amount,
            "applied_delta": applied,
            "population_before": before,
            "population_after": faction.population,
            "neutral_before": neutral_before,
            "neutral_after": context.state.neutral_population,
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        context.event_bus.emit(
            EventType.POPULATION_CHANGED,
            {
                "player_id": None,
                "action_type": reason,
                "target_faction_id": faction_id,
                "population_before": before,
                "population_after": faction.population,
                "neutral_before": neutral_before,
                "neutral_after": context.state.neutral_population,
                "applied_delta": applied,
                "card_id": card_id,
            },
        )
        return payload

    def _v03_apply_neutral_destruction(
        self,
        card_id: str,
        amount: int,
        reason: str,
        context: PhaseContext,
    ) -> dict[str, Any]:
        neutral_before = context.state.neutral_population
        applied = min(amount, context.state.neutral_population)
        context.state.neutral_population -= applied
        payload = {
            "card_id": card_id,
            "effect_type": "v03_neutral_destroyed",
            "reason": reason,
            "requested_delta": -amount,
            "applied_delta": -applied,
            "neutral_before": neutral_before,
            "neutral_after": context.state.neutral_population,
            "destroyed_population_delta": applied,
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        context.event_bus.emit(
            EventType.POPULATION_CHANGED,
            {
                "player_id": None,
                "action_type": reason,
                "target_faction_id": "neutral",
                "population_before": neutral_before,
                "population_after": context.state.neutral_population,
                "neutral_before": neutral_before,
                "neutral_after": context.state.neutral_population,
                "applied_delta": -applied,
                "destroyed_population_delta": applied,
                "card_id": card_id,
            },
        )
        return payload

    def _v03_apply_restoration(
        self,
        card_id: str,
        amount: int,
        controls: dict[str, Any],
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        if controls["prevent_first_restore"] and not controls["restore_prevented_used"]:
            controls["restore_prevented_used"] = True
            payload = {
                "card_id": card_id,
                "effect_type": "v03_population_adjustment",
                "reason": "restore_prevented",
                "requested_delta": amount,
                "applied_delta": 0,
            }
            context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
            return payload
        restore_limit = controls["restore_limit"]
        if restore_limit is not None:
            available_by_limit = max(0, int(restore_limit) - int(controls["restore_used"]))
            amount = min(amount, available_by_limit)
        if amount <= 0:
            return None
        total_population = context.config.population.total_population
        destroyed_population = max(0, total_population - context.state.total_population())
        applied = min(amount, destroyed_population)
        neutral_before = context.state.neutral_population
        context.state.neutral_population += applied
        controls["restore_used"] += applied
        payload = {
            "card_id": card_id,
            "effect_type": "v03_population_restored",
            "reason": "text_restore",
            "requested_delta": amount,
            "applied_delta": applied,
            "neutral_before": neutral_before,
            "neutral_after": context.state.neutral_population,
            "destroyed_population_before": destroyed_population,
            "destroyed_population_after": max(0, total_population - context.state.total_population()),
        }
        context.event_bus.emit(EventType.EFFECT_TRIGGERED, payload)
        if applied:
            context.event_bus.emit(
                EventType.POPULATION_CHANGED,
                {
                    "player_id": None,
                    "action_type": "text_restore",
                    "target_faction_id": "neutral",
                    "population_before": neutral_before,
                    "population_after": context.state.neutral_population,
                    "neutral_before": neutral_before,
                    "neutral_after": context.state.neutral_population,
                    "applied_delta": applied,
                    "card_id": card_id,
                },
            )
        return payload

    def _population_tiebreak(self, selector: Any, context: PhaseContext) -> str:
        selected_value = selector(faction.population for faction in context.state.factions.values())
        candidates = [
            faction_id for faction_id, faction in context.state.factions.items()
            if faction.population == selected_value
        ]
        return self._tiebreak_factions(candidates)

    def _population_tiebreak_excluding(self, selector: Any, context: PhaseContext, excluded: set[str]) -> str:
        candidates_by_value = {
            faction_id: faction.population
            for faction_id, faction in context.state.factions.items()
            if faction_id not in excluded
        }
        selected_value = selector(candidates_by_value.values())
        return self._value_tiebreak(selected_value, candidates_by_value)

    def _propaganda_power_tiebreak(self, selector: Any, powers: dict[str, int], context: PhaseContext, excluded: set[str]) -> str:
        candidates_by_value = {
            faction_id: powers.get(faction_id, 0)
            for faction_id in context.state.factions
            if faction_id not in excluded
        }
        selected_value = selector(candidates_by_value.values())
        return self._value_tiebreak(selected_value, candidates_by_value)

    def _value_tiebreak(self, selected_value: int, values_by_faction: dict[str, int]) -> str:
        candidates = [faction_id for faction_id, value in values_by_faction.items() if value == selected_value]
        return self._tiebreak_factions(candidates)

    def _v03_power_condition_matches(
        self,
        text: str,
        card: CardConfig,
        row: list[str],
        base_power: dict[str, int],
        propaganda_power: dict[str, int],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> bool:
        faction_id = card.faction
        if faction_id not in context.state.factions:
            return False
        if "wenn" not in text and "im " not in text and "solange" not in text:
            return True
        prop_counts = self._research_propaganda_counts(context.state.propaganda_track.get_slots())
        row_factions = [self.cards_by_id[card_id].faction for card_id in row if card_id in self.cards_by_id]
        strengths = [
            int(self.cards_by_id[card_id].strength)
            for card_id in row
            if card_id in self.cards_by_id and self.cards_by_id[card_id].faction == faction_id
        ]
        populations = {key: value.population for key, value in context.state.factions.items()}
        values = list(populations.values())
        total_population = context.config.population.total_population
        destroyed_population = max(0, total_population - context.state.total_population())
        current_round_destroyed = self._event_payload_sum(self._current_round_events(context), "destroyed_population_delta")

        own_adjective = self._v03_faction_adjective(faction_id)
        own_name = self._v03_faction_display_name(faction_id)
        if f"mindestens 1 {own_adjective} propagandakarte" in text and prop_counts.get(faction_id, 0) < 1:
            return False
        if f"mindestens 2 {own_adjective} propagandakarten" in text and prop_counts.get(faction_id, 0) < 2:
            return False
        card_count_match = re.search(rf"mindestens\s+(\d+)\s+{own_adjective}\s+karten", text)
        if card_count_match and row_factions.count(faction_id) < int(card_count_match.group(1)):
            return False
        printed_strength_match = re.search(rf"{own_name}\s+genau\s+(\d+)\s+karten\s+mit\s+gedrucktem\s+machtwert\s+(\d+)", text)
        if printed_strength_match:
            expected_count = int(printed_strength_match.group(1))
            expected_strength = int(printed_strength_match.group(2))
            actual_count = sum(
                1
                for card_id in row
                if card_id in self.cards_by_id
                and self.cards_by_id[card_id].faction == faction_id
                and int(self.cards_by_id[card_id].strength) == expected_strength
            )
            if actual_count != expected_count:
                return False
        if f"{own_name} ein paar" in text and not self._has_pair(strengths):
            return False
        if f"{own_name} einen drilling" in text and not self._has_three_of_a_kind(strengths):
            return False
        if "strasse aus drei machtwerten" in text and not self._has_straight(strengths):
            return False
        if f"{own_name} derzeit nicht die niedrigste macht" in text and total_power.get(faction_id, 0) == min(total_power.values()):
            return False
        if f"{own_name} derzeit nicht die hoechste macht" in text and total_power.get(faction_id, 0) == max(total_power.values()):
            return False
        if f"{own_name} mindestens 10 macht" in text and total_power.get(faction_id, 0) < 10:
            return False
        if f"{own_name} mindestens 12 macht" in text and total_power.get(faction_id, 0) < 12:
            return False
        if f"{own_name} mindestens 15 macht" in text and total_power.get(faction_id, 0) < 15:
            return False
        if "mindestens eine fraktion 12 oder mehr macht" in text and not any(value >= 12 for value in total_power.values()):
            return False
        neutral_match = re.search(r"mindestens\s+(\d+)\s+neutrale\s+bevoelkerung", text)
        if neutral_match and context.state.neutral_population < int(neutral_match.group(1)):
            return False
        if "mindestens 10 neutrale bevoelkerung" in text and context.state.neutral_population < 10:
            return False
        if "mindestens 15 neutrale bevoelkerung" in text and context.state.neutral_population < 15:
            return False
        if "mindestens 20 neutrale bevoelkerung" in text and context.state.neutral_population < 20:
            return False
        if "weniger als 15 neutrale bevoelkerung" in text and context.state.neutral_population >= 15:
            return False
        if "keine fraktion mehr als 15 bevoelkerung" in text and any(value > 15 for value in values):
            return False
        if "alle vier fraktionen bevoelkerung haben" in text and not all(value > 0 for value in values):
            return False
        if "mindestens drei fraktionen karten in der weltgeschichtsreihe" in text and len(set(row_factions)) < 3:
            return False
        if "zwei fraktionen dieselbe bevoelkerung" in text and len(values) == len(set(values)):
            return False
        if "unterschied zwischen der hoechsten und der niedrigsten fraktionsbevoelkerung hoechstens 4" in text and (not values or max(values) - min(values) > 4):
            return False
        if "weder die hoechste noch die niedrigste macht" in text:
            own_power = total_power.get(faction_id, 0)
            if own_power == max(total_power.values()) or own_power == min(total_power.values()):
                return False
        if "mindestens 10 propagandamacht" in text and self._objective_propaganda_power(context.state.propaganda_track.get_slots()).get(faction_id, 0) < 10:
            return False
        if "gelbe propagandamacht aktiviert wird" in text and propaganda_power.get("yellow", 0) <= 0:
            return False
        if "mindestens 1 bevoelkerung vernichtet ist" in text and destroyed_population < 1:
            return False
        if "in dieser weltgeschichte bevoelkerung vernichtet wurde" in text and current_round_destroyed < 1:
            return False
        if "in dieser weltgeschichte mindestens 1 bevoelkerung vernichtet wurde" in text and current_round_destroyed < 1:
            return False
        return True

    def _v03_variable_power_modifier(
        self,
        instance_id: str,
        card: CardConfig,
        text: str,
        row: list[str],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        faction_id = card.faction
        if faction_id not in context.state.factions:
            return None
        amount = 0
        if "fuer jede fraktion mit mindestens 8 bevoelkerung" in text:
            amount = min(4, sum(1 for faction in context.state.factions.values() if faction.population >= 8))
        elif "je 5 neutrale bevoelkerung" in text:
            amount = min(5, context.state.neutral_population // 5)
        elif "fuer jede gelbe propagandakarte" in text:
            amount = self._research_propaganda_counts(context.state.propaganda_track.get_slots()).get("yellow", 0)
        elif "fuer jede rote karte in der weltgeschichtsreihe" in text:
            amount = sum(1 for card_id in row if self._card_faction(card_id) == "red")
        elif "fuer jede schwarze karte mit gedrucktem machtwert 2" in text:
            amount = sum(1 for card_id in row if self._card_faction(card_id) == "black" and self.cards_by_id[card_id].strength == 2)
        elif "fuer jede weitere rote karte mit gedrucktem machtwert 3" in text:
            red_threes = sum(1 for card_id in row if self._card_faction(card_id) == "red" and self.cards_by_id[card_id].strength == 3)
            amount = max(0, red_threes - 1)
        if amount <= 0:
            return None
        return self._apply_v03_power_delta(instance_id, faction_id, amount, "text_variable_power", total_power, context)

    def _v03_power_reduction_target(self, text: str, total_power: dict[str, int], context: PhaseContext) -> tuple[str, int] | None:
        match = re.search(r"senke\s+(?:die\s+)?(?:derzeit\s+)?hoechste\s+macht\s+um\s+(\d+)", text)
        if match:
            return self._power_tiebreak(max(total_power.values()), total_power), int(match.group(1))
        match = re.search(r"senke\s+die\s+macht\s+der\s+fraktion\s+mit\s+der\s+wenigsten\s+bevoelkerung\s+um\s+(\d+)", text)
        if match:
            min_population = min(faction.population for faction in context.state.factions.values())
            candidates = [faction_id for faction_id, faction in context.state.factions.items() if faction.population == min_population]
            return self._tiebreak_factions(candidates), int(match.group(1))
        match = re.search(r"senke\s+die\s+macht\s+der\s+fraktion\s+mit\s+der\s+hoechsten\s+propagandamacht\s+um\s+(\d+)", text)
        if match:
            prop_power = self._objective_propaganda_power(context.state.propaganda_track.get_slots())
            return self._power_tiebreak(max(prop_power.values()), prop_power), int(match.group(1))
        for faction_name, faction_id in self._v03_faction_name_map().items():
            match = re.search(rf"senke\s+{faction_name}\s+um\s+(\d+)\s+macht", text)
            if match:
                return faction_id, int(match.group(1))
        return None

    def _power_tiebreak(self, value: int, powers: dict[str, int]) -> str:
        candidates = [faction_id for faction_id, power in powers.items() if power == value]
        return self._tiebreak_factions(candidates)

    def _tiebreak_factions(self, candidates: list[str]) -> str:
        for faction in self.config.factions:
            if faction.id in candidates:
                return faction.id
        return candidates[0]

    def _v03_slot_condition_matches(self, text: str, slot_index: int | None) -> bool:
        if slot_index is None:
            return True
        slot_number = slot_index + 1
        slot_words = {"ersten": 1, "zweiten": 2, "dritten": 3, "vierten": 4}
        mentioned = [number for word, number in slot_words.items() if f"im {word} propagandaslot" in text]
        if "im dritten oder vierten propagandaslot" in text:
            mentioned.extend([3, 4])
        if "im zweiten oder dritten propagandaslot" in text:
            mentioned.extend([2, 3])
        return not mentioned or slot_number in set(mentioned)

    def _v03_faction_name_map(self) -> dict[str, str]:
        return {"rot": "red", "schwarz": "black", "gelb": "yellow", "gruen": "green"}

    def _v03_faction_display_name(self, faction_id: str) -> str:
        return {"red": "rot", "black": "schwarz", "yellow": "gelb", "green": "gruen"}[faction_id]

    def _v03_faction_adjective(self, faction_id: str) -> str:
        return {"red": "rote", "black": "schwarze", "yellow": "gelbe", "green": "gruene"}[faction_id]

    def _world_history_transitions(self, row: list[str]) -> list[tuple[str | None, str | None]]:
        transitions: list[tuple[str | None, str | None]] = []
        factions = [self.cards_by_id[card_id].faction if card_id in self.cards_by_id else None for card_id in row]
        if len(factions) == 1:
            transitions.append((None, factions[0]))
        for left, right in zip(factions, factions[1:]):
            transitions.append((left, right))
        return transitions

    def _v03_attack_pairs(
        self,
        row: list[str],
        total_power: dict[str, int],
        interpretation_authority: str | None,
        context: PhaseContext,
    ) -> tuple[list[tuple[str, str | None]], list[dict[str, Any]]]:
        markers: dict[str, dict[str, int]] = {faction_id: {} for faction_id in context.state.factions}
        target_effects: list[dict[str, Any]] = []
        for attacker, defender in self._world_history_transitions(row):
            if attacker is not None and defender is not None and attacker != defender:
                self._v03_add_target_marker(
                    markers,
                    attacker,
                    defender,
                    1,
                    {
                        "effect_type": "v03_target_marker",
                        "reason": "world_history_transition",
                        "attacker": attacker,
                        "defender": defender,
                        "marker_count": 1,
                    },
                    target_effects,
                )
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is None or not card.effect_text:
                continue
            effect = self._v03_target_from_text(card_id, card, row, total_power, context)
            if effect is None:
                continue
            if self._v03_target_change_is_ignored(effect, row, context):
                ignored = {
                    "card_id": effect["card_id"],
                    "effect_type": "v03_target_modifier_ignored",
                    "reason": "first_target_change_to_faction_ignored",
                    "attacker": effect["attacker"],
                    "defender": effect["defender"],
                }
                target_effects.append(ignored)
                context.event_bus.emit(EventType.EFFECT_TRIGGERED, ignored)
                continue
            effect["marker_count"] = 2
            self._v03_add_target_marker(markers, effect["attacker"], effect["defender"], 2, effect, target_effects)
            target_effects.append(effect)
            context.event_bus.emit(EventType.EFFECT_TRIGGERED, effect)
        for card_id in context.state.propaganda_track.get_slots():
            card = self.cards_by_id.get(card_id or "")
            if card is None or not card.effect_text:
                continue
            effect = self._v03_target_from_text(str(card_id), card, row, total_power, context)
            if effect is None:
                continue
            if self._v03_target_change_is_ignored(effect, row, context):
                ignored = {
                    "card_id": effect["card_id"],
                    "effect_type": "v03_target_modifier_ignored",
                    "reason": "first_target_change_to_faction_ignored",
                    "attacker": effect["attacker"],
                    "defender": effect["defender"],
                }
                target_effects.append(ignored)
                context.event_bus.emit(EventType.EFFECT_TRIGGERED, ignored)
                continue
            effect["marker_count"] = 3
            self._v03_add_target_marker(markers, effect["attacker"], effect["defender"], 3, effect, target_effects)
            target_effects.append(effect)
            context.event_bus.emit(EventType.EFFECT_TRIGGERED, effect)
        target_summary = {
            attacker: dict(defenders)
            for attacker, defenders in markers.items()
            if defenders
        }
        if target_summary:
            target_effects.append(
                {
                    "effect_type": "v03_target_marker_summary",
                    "reason": "target_markers_collected",
                    "markers": target_summary,
                    "interpretation_authority": interpretation_authority,
                }
            )
        pairs: list[tuple[str, str | None]] = []
        for attacker, defenders in markers.items():
            if not defenders:
                continue
            defender = self._v03_choose_marker_target(attacker, defenders, interpretation_authority)
            if defender is not None:
                pairs.append((attacker, defender))
        return pairs, target_effects

    def _v03_add_target_marker(
        self,
        markers: dict[str, dict[str, int]],
        attacker: str,
        defender: str | None,
        amount: int,
        effect: dict[str, Any],
        target_effects: list[dict[str, Any]],
    ) -> None:
        if attacker not in markers or defender not in markers or attacker == defender:
            return
        markers[attacker][defender] = markers[attacker].get(defender, 0) + amount

    def _v03_choose_marker_target(
        self,
        attacker: str,
        defenders: dict[str, int],
        interpretation_authority: str | None,
    ) -> str | None:
        if not defenders:
            return None
        highest = max(defenders.values())
        candidates = [defender for defender, value in defenders.items() if value == highest and defender != attacker]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if interpretation_authority in candidates:
            return interpretation_authority
        return self._tiebreak_factions(candidates)

    def _v03_interpretation_authority(self, activated_propaganda_power: dict[str, int]) -> str | None:
        positive = {faction_id: power for faction_id, power in activated_propaganda_power.items() if power > 0}
        if not positive:
            return None
        highest = max(positive.values())
        candidates = [faction_id for faction_id, power in positive.items() if power == highest]
        return self._tiebreak_factions(candidates)

    def _v03_target_change_is_ignored(self, effect: dict[str, Any], row: list[str], context: PhaseContext) -> bool:
        defender = effect.get("defender")
        if defender not in context.state.factions:
            return False
        if not self._v03_faction_ignores_first_target_change(str(defender), row, context):
            return False
        return not any(
            event.event_type == EventType.EFFECT_TRIGGERED
            and event.payload.get("effect_type") == "v03_target_modifier_ignored"
            and event.payload.get("defender") == defender
            for event in self._current_round_events(context)
        )

    def _v03_faction_ignores_first_target_change(self, faction_id: str, row: list[str], context: PhaseContext) -> bool:
        sources: list[str] = list(row) + [card_id for card_id in context.state.propaganda_track.get_slots() if card_id is not None]
        faction_name = self._v03_faction_display_name(faction_id)
        for card_id in sources:
            card = self.cards_by_id.get(card_id)
            if card is None or not card.effect_text:
                continue
            text = self._normalize_condition(card.effect_text)
            if f"erste zielaenderung, die {faction_name} zum ziel eines angriffs machen wuerde, wird ignoriert" in text:
                return True
        return False

    def _apply_v03_combat_text_modifiers(
        self,
        row: list[str],
        attack_records: list[dict[str, Any]],
        total_power: dict[str, int],
        interpretation_authority: str | None,
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        modifiers: list[dict[str, Any]] = []
        sources: list[tuple[str, CardConfig, int | None]] = []
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is not None:
                sources.append((card_id, card, None))
        for slot_index, card_id in enumerate(context.state.propaganda_track.get_slots()):
            card = self.cards_by_id.get(card_id or "")
            if card is not None:
                sources.append((str(card_id), card, slot_index))

        for instance_id, card, slot_index in sources:
            if not card.effect_text:
                continue
            text = self._normalize_condition(card.effect_text)
            if "kampfwirkung" not in text and "angriff" not in text and "neutralisierte bevoelkerung stattdessen vernichtet" not in text:
                continue
            if slot_index is None and "solange diese karte als propaganda ausliegt" in text:
                continue
            if not self._v03_slot_condition_matches(text, slot_index):
                continue
            if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
                continue
            produced = self._v03_combat_modifiers_from_text(instance_id, card, text, attack_records, total_power, interpretation_authority, context)
            for modifier in produced:
                modifiers.append(modifier)
                context.event_bus.emit(EventType.EFFECT_TRIGGERED, modifier)
        return modifiers

    def _v03_combat_modifiers_from_text(
        self,
        instance_id: str,
        card: CardConfig,
        text: str,
        attack_records: list[dict[str, Any]],
        total_power: dict[str, int],
        interpretation_authority: str | None,
        context: PhaseContext,
    ) -> list[dict[str, Any]]:
        if not attack_records:
            return []
        modifiers: list[dict[str, Any]] = []
        faction_id = card.faction
        faction_name_map = self._v03_faction_name_map()

        power_penalty_target = self._v03_attack_power_penalty_target(text, faction_name_map)
        if power_penalty_target is not None:
            amount_match = re.search(r"mit\s+-(\d+)\s+macht des angreifers", text)
            amount = int(amount_match.group(1)) if amount_match else 0
            record = self._first_attack_record(attack_records, defender=power_penalty_target)
            if record is not None and amount > 0:
                before_margin = int(record["margin"])
                before_impact = int(record["impact"])
                record["margin"] = before_margin - amount
                record["base_impact"] = self._combat_impact(int(record["margin"]))
                record["impact"] = min(int(record["impact"]), int(record["base_impact"]))
                modifier = {
                    "card_id": instance_id,
                    "effect_type": "v03_combat_modifier",
                    "reason": "attack_power_penalty",
                    "attacker": record["attacker"],
                    "defender": record["defender"],
                    "amount": -amount,
                    "margin_before": before_margin,
                    "margin_after": int(record["margin"]),
                    "before": before_impact,
                    "after": int(record["impact"]),
                }
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        prevent_target = self._v03_prevented_attack_target(text, faction_name_map)
        if prevent_target is not None:
            record = self._first_attack_record(attack_records, defender=prevent_target)
            if record is not None:
                before = int(record["impact"])
                record["impact"] = 0
                record["prevented"] = True
                modifier = self._v03_record_combat_modifier(instance_id, "prevent_first_attack", record, -before, before, int(record["impact"]))
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        reduction_target = self._v03_reduced_attack_target(text, faction_name_map)
        if reduction_target is not None:
            amount = self._v03_reduction_amount(text, reduction_target, attack_records, context)
            record = self._first_attack_record(attack_records, defender=reduction_target, min_impact=1)
            if record is not None and amount > 0:
                before = int(record["impact"])
                record["impact"] = max(0, before - amount)
                modifier = self._v03_record_combat_modifier(instance_id, "reduce_first_impact", record, -min(amount, before), before, int(record["impact"]))
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        less_loss_target = self._v03_less_loss_target(text, faction_id)
        if less_loss_target is not None:
            if "von mehr als einer fraktion angegriffen" in text:
                attacker_count = sum(1 for record in attack_records if record["defender"] == less_loss_target)
                if attacker_count <= 1:
                    less_loss_target = None
            if less_loss_target is not None and f"wenn {self._v03_faction_display_name(less_loss_target)}" in text:
                if not self._v03_power_condition_matches(text, card, [], total_power, total_power, total_power, context):
                    less_loss_target = None
        if less_loss_target is not None:
            record = self._first_attack_record(attack_records, defender=less_loss_target, min_impact=1)
            if record is not None:
                before = int(record["impact"])
                record["impact"] = max(0, before - 1)
                modifier = self._v03_record_combat_modifier(instance_id, "lose_one_less_population", record, -min(1, before), before, int(record["impact"]))
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        if "erste erfolgreiche angriff einer fraktion mit mindestens 12 macht verursacht 1 weniger kampfwirkung" in text:
            record = next((candidate for candidate in attack_records if total_power.get(str(candidate["attacker"]), 0) >= 12 and int(candidate.get("impact", 0)) > 0), None)
            if record is not None:
                before = int(record["impact"])
                record["impact"] = max(0, before - 1)
                modifier = self._v03_record_combat_modifier(instance_id, "high_power_attack_reduced", record, -min(1, before), before, int(record["impact"]))
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        added_impact = self._v03_added_impact_amount(text)
        boosted_attacker = self._v03_boosted_attacker(text, faction_id)
        if added_impact > 0 and boosted_attacker is not None:
            records = [record for record in attack_records if record["attacker"] == boosted_attacker and int(record.get("impact", 0)) > 0]
            if "ersten erfolgreichen" in text or "eines erfolgreichen" in text or "dessen kampfwirkung" in text:
                records = records[:1]
            for record in records:
                if "ziel auf 5 oder weniger bevoelkerung bringt" in text:
                    defender_population = context.state.factions[str(record["defender"])].population
                    if defender_population - int(record["impact"]) > 5:
                        continue
                before = int(record["impact"])
                record["impact"] = before + added_impact
                modifier = self._v03_record_combat_modifier(instance_id, "add_combat_impact", record, added_impact, before, int(record["impact"]))
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        destroyed_amount = self._v03_destroyed_instead_amount(text)
        destroyed_attacker = self._v03_boosted_attacker(text, faction_id)
        if destroyed_amount > 0 and destroyed_attacker is not None:
            records = [record for record in attack_records if record["attacker"] == destroyed_attacker and int(record.get("impact", 0)) > 0]
            for record in records[:1]:
                margin_match = re.search(r"mindestens\s+(\d+)\s+macht vorsprung", text)
                if margin_match and int(record.get("margin", 0)) < int(margin_match.group(1)):
                    continue
                if record.get("prevented"):
                    continue
                if record.get("replacement_effect") is not None:
                    ignored = self._v03_record_combat_modifier(instance_id, "replacement_ignored", record, 0, int(record.get("destroyed_impact", 0)), int(record.get("destroyed_impact", 0)))
                    ignored["ignored_by"] = record["replacement_effect"]["card_id"]
                    record["modifiers"].append(ignored)
                    modifiers.append(ignored)
                    continue
                before = int(record.get("destroyed_impact", 0))
                record["destroyed_impact"] = min(int(record["impact"]), before + destroyed_amount)
                modifier = self._v03_record_combat_modifier(
                    instance_id,
                    "destroy_instead_of_neutralize",
                    record,
                    int(record["destroyed_impact"]) - before,
                    before,
                    int(record["destroyed_impact"]),
                )
                modifier["interpretation_authority"] = interpretation_authority
                record["replacement_effect"] = modifier
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        transfer = self._v03_transfer_replacement(text, faction_id)
        if transfer is not None:
            transfer_attacker, transfer_target, transfer_amount = transfer
            records = [record for record in attack_records if record["attacker"] == transfer_attacker and int(record.get("impact", 0)) > 0]
            for record in records[:1]:
                if record.get("prevented"):
                    continue
                if context.state.factions[str(record["defender"])].population >= context.state.factions[transfer_target].population:
                    continue
                if record.get("replacement_effect") is not None:
                    ignored = self._v03_record_combat_modifier(instance_id, "replacement_ignored", record, 0, int(record.get("transferred_impact", 0)), int(record.get("transferred_impact", 0)))
                    ignored["ignored_by"] = record["replacement_effect"]["card_id"]
                    record["modifiers"].append(ignored)
                    modifiers.append(ignored)
                    continue
                before = int(record.get("transferred_impact", 0))
                record["transferred_impact"] = min(int(record["impact"]), before + transfer_amount)
                record["transfer_target_faction"] = transfer_target
                modifier = self._v03_record_combat_modifier(
                    instance_id,
                    "transfer_instead_of_neutralize",
                    record,
                    int(record["transferred_impact"]) - before,
                    before,
                    int(record["transferred_impact"]),
                )
                modifier["transfer_target_faction"] = transfer_target
                modifier["interpretation_authority"] = interpretation_authority
                record["replacement_effect"] = modifier
                record["modifiers"].append(modifier)
                modifiers.append(modifier)

        return modifiers

    def _v03_prevented_attack_target(self, text: str, faction_name_map: dict[str, str]) -> str | None:
        if "wird verhindert" not in text:
            return None
        for faction_name, faction_id in faction_name_map.items():
            if f"angriff gegen {faction_name}" in text:
                return faction_id
        return None

    def _v03_attack_power_penalty_target(self, text: str, faction_name_map: dict[str, str]) -> str | None:
        if "macht des angreifers" not in text:
            return None
        for faction_name, faction_id in faction_name_map.items():
            if f"angriff gegen {faction_name}" in text:
                return faction_id
        return None

    def _v03_reduced_attack_target(self, text: str, faction_name_map: dict[str, str]) -> str | None:
        if "reduziere die erste kampfwirkung gegen" not in text:
            return None
        for faction_name, faction_id in faction_name_map.items():
            if f"gegen {faction_name}" in text:
                return faction_id
        return None

    def _v03_less_loss_target(self, text: str, fallback_faction_id: str | None) -> str | None:
        if "verliert durch den ersten erfolgreichen angriff 1 weniger bevoelkerung" in text:
            return fallback_faction_id
        if "verliert 1 weniger bevoelkerung" in text:
            return fallback_faction_id
        for faction_id in self.state.factions:
            display_name = self._v03_faction_display_name(faction_id)
            if f"verliert {display_name} 1 weniger bevoelkerung" in text:
                return faction_id
        return None

    def _v03_reduction_amount(
        self,
        text: str,
        defender: str,
        attack_records: list[dict[str, Any]],
        context: PhaseContext,
    ) -> int:
        amount_match = re.search(r"reduziere die erste kampfwirkung gegen \w+ um (\d+)", text)
        amount = int(amount_match.group(1)) if amount_match else 1
        if "stattdessen um 2" in text:
            record = self._first_attack_record(attack_records, defender=defender)
            if record is not None:
                attacker_population = context.state.factions[str(record["attacker"])].population
                defender_population = context.state.factions[defender].population
                if defender_population < attacker_population:
                    amount = 2
        return amount

    def _v03_added_impact_amount(self, text: str) -> int:
        match = re.search(r"kampfwirkung\s+um\s+(\d+)", text)
        if match:
            return int(match.group(1))
        match = re.search(r"kampfwirkung", text)
        if match:
            plus_match = re.search(r"\+(\d+)\s+kampfwirkung", text)
            if plus_match:
                return int(plus_match.group(1))
        match = re.search(r"neutralisieren\s+(\d+)\s+zusaetzliche bevoelkerung", text)
        if match:
            return int(match.group(1))
        match = re.search(r"neutralisiere zusaetzlich\s+(\d+)\s+bevoelkerung", text)
        if match:
            return int(match.group(1))
        return 0

    def _v03_destroyed_instead_amount(self, text: str) -> int:
        if "neutralisierte bevoelkerung stattdessen vernichtet" not in text:
            return 0
        match = re.search(r"(?:wird\s+)?(\d+)\s+durch .*? neutralisierte bevoelkerung stattdessen vernichtet", text)
        return int(match.group(1)) if match else 1

    def _v03_transfer_replacement(self, text: str, fallback_faction_id: str | None) -> tuple[str, str, int] | None:
        if "neutralisierte bevoelkerung stattdessen direkt zu" not in text:
            return None
        attacker = fallback_faction_id
        target = None
        for faction_id in self.state.factions:
            display = self._v03_faction_display_name(faction_id)
            if f"angriff von {display}" in text:
                attacker = faction_id
            if f"direkt zu {display}" in text:
                target = faction_id
        match = re.search(r"(\d+)\s+durch .*? neutralisierte bevoelkerung stattdessen direkt zu", text)
        amount = int(match.group(1)) if match else 1
        if attacker is None or target is None:
            return None
        return attacker, target, amount

    def _v03_boosted_attacker(self, text: str, fallback_faction_id: str | None) -> str | None:
        for faction_id in self.state.factions:
            adjective = self._v03_faction_adjective(faction_id)
            display_name = self._v03_faction_display_name(faction_id)
            if adjective in text and "angriff" in text:
                return faction_id
            if f"wenn {display_name} einen erfolgreichen angriff" in text:
                return faction_id
            if f"wenn {display_name} " in text and "erfolgreich angreift" in text:
                return faction_id
        if "erfolgreichen angriff" in text or "erfolgreiche" in text:
            return fallback_faction_id
        return None

    def _first_attack_record(
        self,
        attack_records: list[dict[str, Any]],
        *,
        attacker: str | None = None,
        defender: str | None = None,
        min_impact: int = 0,
    ) -> dict[str, Any] | None:
        for record in attack_records:
            if attacker is not None and record["attacker"] != attacker:
                continue
            if defender is not None and record["defender"] != defender:
                continue
            if int(record.get("impact", 0)) < min_impact:
                continue
            return record
        return None

    def _v03_record_combat_modifier(
        self,
        card_id: str,
        reason: str,
        record: dict[str, Any],
        amount: int,
        before: int,
        after: int,
    ) -> dict[str, Any]:
        return {
            "card_id": card_id,
            "effect_type": "v03_combat_modifier",
            "reason": reason,
            "attacker": record["attacker"],
            "defender": record["defender"],
            "amount": amount,
            "before": before,
            "after": after,
        }

    def _v03_target_from_text(
        self,
        instance_id: str,
        card: CardConfig,
        row: list[str],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> dict[str, Any] | None:
        text = self._normalize_condition(card.effect_text or "")
        if "greift" not in text:
            return None
        if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
            return None
        attacker = card.faction
        if "die fraktion mit der hoechsten macht greift" in text:
            attacker = self._power_tiebreak(max(total_power.values()), total_power)
        if attacker not in context.state.factions:
            return None
        defender: str | None = None
        reason = "text_target"
        if "nicht-rote fraktion mit der niedrigsten bevoelkerung" in text:
            defender = self._population_tiebreak_excluding(min, context, {"red"})
            reason = "text_target_lowest_population_excluding_red"
        elif "fraktion mit der wenigsten bevoelkerung" in text:
            defender = self._population_tiebreak_excluding(min, context, {attacker})
            reason = "text_target_lowest_population"
        elif "fraktion mit der niedrigsten bevoelkerung" in text:
            defender = self._population_tiebreak_excluding(min, context, {attacker})
            reason = "text_target_lowest_population"
        elif "fraktion mit der meisten bevoelkerung" in text:
            defender = self._population_tiebreak_excluding(max, context, {attacker})
            reason = "text_target_highest_population"
        elif "fraktion mit der niedrigsten propagandamacht" in text:
            prop_power = self._objective_propaganda_power(context.state.propaganda_track.get_slots())
            defender = self._propaganda_power_tiebreak(min, prop_power, context, {attacker})
            reason = "text_target_lowest_propaganda_power"
        elif "fraktion mit der hoechsten propagandamacht" in text:
            prop_power = self._objective_propaganda_power(context.state.propaganda_track.get_slots())
            defender = self._propaganda_power_tiebreak(max, prop_power, context, {attacker})
            reason = "text_target_highest_propaganda_power"
        elif "fraktion an, die in dieser weltgeschichtsreihe die wenigsten karten hat" in text:
            row_factions = [self._card_faction(card_id) for card_id in row]
            counts = {faction_id: row_factions.count(faction_id) for faction_id in context.state.factions if faction_id != attacker}
            defender = self._value_tiebreak(min(counts.values()), counts)
            reason = "text_target_fewest_world_history_cards"
        if defender is None or defender == attacker:
            return None
        return {
            "card_id": instance_id,
            "effect_type": "v03_target_modifier",
            "reason": reason,
            "attacker": attacker,
            "defender": defender,
        }

    def _v03_combat_tiebreakers(
        self,
        row: list[str],
        total_power: dict[str, int],
        context: PhaseContext,
    ) -> dict[str, list[str]]:
        tiebreakers: dict[str, list[str]] = {}
        sources: list[tuple[str, CardConfig, int | None, Literal["world_history", "propaganda"]]] = []
        for card_id in row:
            card = self.cards_by_id.get(card_id)
            if card is not None:
                sources.append((card_id, card, None, "world_history"))
        for slot_index, card_id in enumerate(context.state.propaganda_track.get_slots()):
            card = self.cards_by_id.get(card_id or "")
            if card is not None:
                sources.append((str(card_id), card, slot_index, "propaganda"))
        for instance_id, card, slot_index, source in sources:
            text = self._normalize_condition(card.effect_text or "")
            if "machtgleichstaende in kaempfen" not in text:
                continue
            if source == "world_history" and "solange diese karte als propaganda ausliegt" in text:
                continue
            if source == "propaganda" and not self._v03_slot_condition_matches(text, slot_index):
                continue
            if not self._v03_power_condition_matches(text, card, row, total_power, total_power, total_power, context):
                continue
            for faction_name, faction_id in self._v03_faction_name_map().items():
                if faction_name in text and faction_id in context.state.factions:
                    tiebreakers.setdefault(faction_id, []).append(instance_id)
                    context.event_bus.emit(
                        EventType.EFFECT_TRIGGERED,
                        {
                            "card_id": instance_id,
                            "effect_type": "v03_combat_tiebreaker",
                            "reason": "win_power_ties_in_combat",
                            "target_faction_id": faction_id,
                        },
                    )
                    break
        return tiebreakers

    def _combat_impact(self, margin: int, *, tie_won: bool = False) -> int:
        if tie_won:
            return 1
        bands = self.config.v03.get("combat", {}).get("impact_by_margin", [])
        for band in bands:
            minimum = int(band.get("min", 0))
            maximum = band.get("max")
            if margin >= minimum and (maximum is None or margin <= int(maximum)):
                return int(band.get("impact", 0))
        if margin >= 12:
            return 4
        if margin >= 8:
            return 3
        if margin >= 4:
            return 2
        return 1 if margin >= 1 else 0

    def _research_order_is_fulfilled(self, card_id: str, context: PhaseContext) -> bool:
        card = self.cards_by_id.get(card_id)
        if card is None or not card.condition:
            fulfilled = False
            condition = None
        else:
            condition = card.condition
            fulfilled = self._evaluate_research_condition(condition, context)
        context.event_bus.emit(
            EventType.RESEARCH_ASSIGNMENTS_CHECKED,
            {
                "card_id": card_id,
                "condition": condition,
                "fulfilled": fulfilled,
            },
        )
        return fulfilled

    def _evaluate_research_condition(self, condition: str, context: PhaseContext) -> bool:
        text = self._normalize_condition(condition)
        populations = {faction_id: faction.population for faction_id, faction in context.state.factions.items()}
        values = list(populations.values())
        round_events = self._current_round_events(context)
        world_power = self._latest_payload(round_events, EventType.WORLD_HISTORY_POWER_CALCULATED)
        combat = self._latest_payload(round_events, EventType.COMBAT_RESOLVED)
        total_power = {key: int(value) for key, value in (world_power.get("total_power") or {}).items()}
        row = list(context.state.world_history_row)
        if not row:
            row = list(self._latest_payload(round_events, EventType.WORLD_HISTORY_REVEALED).get("card_ids") or [])
        row_factions = [self.cards_by_id[card_id].faction for card_id in row if card_id in self.cards_by_id]
        row_strengths = {
            faction_id: [
                int(self.cards_by_id[card_id].strength)
                for card_id in row
                if card_id in self.cards_by_id and self.cards_by_id[card_id].faction == faction_id
            ]
            for faction_id in context.state.factions
        }
        transitions = self._world_history_transitions(row)
        attack_records = self._research_attack_records(row, total_power)
        applied_deltas = {key: int(value) for key, value in (combat.get("applied_deltas") or {}).items()}
        requested_deltas = {key: int(value) for key, value in (combat.get("requested_deltas") or {}).items()}
        prop_slots = context.state.propaganda_track.get_slots()
        prop_counts = self._research_propaganda_counts(prop_slots)
        objective_prop_power = self._objective_propaganda_power(prop_slots)
        active_population = context.state.total_population()
        destroyed_population = max(0, int(context.config.population.total_population) - active_population)

        if "mindestens 60 neutrale bevoelkerung" in text:
            return context.state.neutral_population >= 60
        if "weniger als 65 neutrale bevoelkerung" in text:
            return context.state.neutral_population < 65
        if "5 oder weniger bevoelkerung" in text and "keine fraktion" not in text and "jeder fraktion" not in text:
            return any(value <= 5 for value in values)
        if "mindestens 12 bevoelkerung" in text and "fraktion hat" in text:
            return any(value >= 12 for value in values)
        if "alle vier fraktionen mindestens 1 bevoelkerung" in text:
            return len(values) >= 4 and all(value >= 1 for value in values)
        if "2 oder mehr bevoelkerung rekrutiert" in text:
            return any(delta >= 2 for delta in applied_deltas.values())
        if "insgesamt mindestens 2 bevoelkerung neutralisiert" in text:
            return sum(abs(delta) for delta in applied_deltas.values() if delta < 0) >= 2
        if "bevoelkerung verloren und bevoelkerung rekrutiert" in text:
            return any(delta < 0 for delta in applied_deltas.values()) and any(delta > 0 for delta in applied_deltas.values())
        if "mindestens 13 finale macht" in text:
            return any(value >= 13 for value in total_power.values())
        if "keine fraktion erreicht" in text and "mehr als 10 finale macht" in text:
            return bool(total_power) and all(value <= 10 for value in total_power.values())
        if "hoechstens 3 macht vorsprung" in text:
            return any(0 < record["margin"] <= 3 and record["impact"] > 0 for record in attack_records)
        if "mindestens drei fraktionen" in text and "angriffsziel" in text:
            attackers = {attacker for attacker, defender in transitions if attacker and defender and attacker != defender}
            return len(attackers) >= 3
        if "macht, aber kein angriffsziel" in text:
            return any(delta > 0 for key, delta in requested_deltas.items() if key in context.state.factions)
        if "angriff wird verhindert" in text or "kampfwirkung wird auf 0" in text:
            return any(record["margin"] <= 0 or record["impact"] == 0 for record in attack_records)
        if "dieselbe finale macht" in text:
            living_powers = [
                total_power.get(faction_id, 0)
                for faction_id, population in populations.items()
                if population > 0 and total_power.get(faction_id, 0) >= 0
            ]
            return len(living_powers) != len(set(living_powers))
        if "neue propaganda einer fraktion" in text and "vorher keine propagandakarte" in text:
            starting = self._round_start_payload(round_events).get("propaganda_slots") or []
            starting_factions = set(self._research_propaganda_counts(starting))
            for event in round_events:
                if event.event_type == EventType.PROPAGANDA_PLACED:
                    faction_id = self._card_faction(event.payload.get("card_id"))
                    if faction_id is not None and faction_id not in starting_factions:
                        return True
            return False
        if "dieselbe propagandakarte in slot 4" in text:
            starting = self._round_start_payload(round_events).get("propaganda_slots") or []
            return len(starting) >= 4 and len(prop_slots) >= 4 and starting[3] is not None and starting[3] == prop_slots[3]
        if "mindestens eine propagandakarte entfernt" in text:
            return any(event.event_type == EventType.PROPAGANDA_REMOVED for event in round_events)
        if "mindestens eine propagandakarte verdraengt" in text:
            return any(
                event.event_type == EventType.PROPAGANDA_REMOVED and "displaced" in str(event.payload.get("reason", ""))
                for event in round_events
            )
        if "mindestens zwei propagandakarten derselben fraktion" in text:
            return any(count >= 2 for count in prop_counts.values())
        if "mindestens drei verschiedenen fraktionen" in text:
            return len([faction_id for faction_id, count in prop_counts.items() if count > 0]) >= 3
        if "weniger als drei propagandakarten" in text:
            return sum(1 for card_id in prop_slots if card_id is not None) < 3
        if "propaganda in der leiste" in text and "keine karte in der weltgeschichtsreihe" in text:
            return any(faction_id not in set(row_factions) for faction_id in prop_counts)
        if "bleibt vom beginn bis zum ende der runde im selben propagandaslot" in text:
            starting = self._round_start_payload(round_events).get("propaganda_slots") or []
            return any(card_id is not None and index < len(prop_slots) and prop_slots[index] == card_id for index, card_id in enumerate(starting))
        if "quelle gegen den journalisten eingesetzt" in text:
            return any(event.event_type == EventType.SOURCE_SPENT and event.payload.get("purpose") == "journalist_challenge" for event in round_events)
        if "journalist bleibt im amt" in text and "quelle gegen ihn" in text:
            payload = self._latest_payload(round_events, EventType.JOURNALIST_CHECKED)
            return int(payload.get("challenger_total") or 0) >= 1 and payload.get("changed") is False
        if "medienmogulwahl entsteht ein gleichstand" in text:
            payload = self._latest_payload(round_events, EventType.MEDIA_MOGUL_CHANGED)
            return len(payload.get("tied_candidates") or []) > 1
        if "medienmogul dieser runde war in der vorrunde nicht medienmogul" in text:
            payload = self._latest_payload(round_events, EventType.MEDIA_MOGUL_CHANGED)
            return bool(payload) and payload.get("old_media_mogul_player_id") != payload.get("new_media_mogul_player_id")
        if "keine fraktion" in text and "alleinige hoechste macht" in text:
            return bool(total_power) and list(total_power.values()).count(max(total_power.values())) > 1
        if "alle vier fraktionen haben mindestens eine karte" in text:
            return set(context.state.factions).issubset(set(row_factions))
        if "ein paar" in text:
            return any(self._has_pair(strengths) for strengths in row_strengths.values())
        if "verliert keine fraktion bevoelkerung" in text:
            return not any(delta < 0 for delta in applied_deltas.values())
        if "mindestens 1 bevoelkerung vernichtet" in text:
            return destroyed_population >= 1 or self._event_payload_sum(round_events, "destroyed_population_delta") >= 1
        if "mindestens 1 bevoelkerung wiederhergestellt" in text:
            return self._event_payload_sum(round_events, "restored_population_delta") >= 1
        if "weniger als 95 bevoelkerung im aktiven spiel" in text:
            return active_population < 95
        if "keine fraktion hat" in text and "5 oder weniger bevoelkerung" in text:
            return all(value > 5 for value in values)
        if "unterschied zwischen der fraktion mit der meisten bevoelkerung" in text and "mindestens 12" in text:
            return bool(values) and max(values) - min(values) >= 12
        if "unterschied zwischen der fraktion mit der meisten bevoelkerung" in text and "hoechstens 3" in text:
            return bool(values) and max(values) - min(values) <= 3
        if "4 oder mehr bevoelkerung rekrutiert" in text:
            return any(delta >= 4 for delta in applied_deltas.values())
        if "insgesamt mindestens 4 bevoelkerung neutralisiert" in text:
            return sum(abs(delta) for delta in applied_deltas.values() if delta < 0) >= 4
        if "mindestens 8 macht vorsprung" in text:
            return any(record["margin"] >= 8 and record["impact"] > 0 for record in attack_records)
        if "kampfwirkung betraegt" in text and "mindestens 4" in text:
            return any(record["impact"] >= 4 for record in attack_records)
        if "ziel von mindestens zwei angriffen" in text:
            defenders = [defender for _, defender in transitions if defender is not None]
            return any(defenders.count(faction_id) >= 2 for faction_id in context.state.factions)
        if "mindestens 10 finaler macht" in text and "keinen erfolgreichen angriff" in text:
            successful_attackers = {record["attacker"] for record in attack_records if record["impact"] > 0}
            return any(power >= 10 and faction_id not in successful_attackers for faction_id, power in total_power.items())
        if "mindestens zwei karteneffekte" in text:
            return sum(1 for event in round_events if event.event_type == EventType.EFFECT_TRIGGERED) >= 2
        if "mindestens 12 propagandamacht" in text:
            return any(value >= 12 for value in objective_prop_power.values())
        if "verliert in dieser runde ihre letzte propagandakarte" in text:
            removed_factions = {
                self._card_faction(event.payload.get("card_id"))
                for event in round_events
                if event.event_type == EventType.PROPAGANDA_REMOVED
            }
            return any(faction_id is not None and prop_counts.get(faction_id, 0) == 0 for faction_id in removed_factions)
        if "propagandakarte verschoben" in text:
            return any("moved" in str(event.event_type) or "verschob" in str(event.payload).lower() for event in round_events)
        if "mindestens 3 stimmen" in text:
            payload = self._latest_payload(round_events, EventType.MEDIA_MOGUL_CHANGED)
            return any(int(count) >= 3 for count in (payload.get("counts") or {}).values())
        if "journalist entscheidet einen gleichstand" in text:
            payload = self._latest_payload(round_events, EventType.MEDIA_MOGUL_CHANGED)
            return payload.get("tie_breaker") == "journalist"
        if "mindestens 4 karten in der weltgeschichtsreihe" in text:
            return any(row_factions.count(faction_id) >= 4 for faction_id in context.state.factions)
        if "strasse aus drei machtwerten" in text:
            return any(self._has_straight(strengths) for strengths in row_strengths.values())
        if "mindestens 3 bevoelkerung vernichtet" in text:
            return destroyed_population >= 3 or self._event_payload_sum(round_events, "destroyed_population_delta") >= 3
        if "genau 1 bevoelkerung" in text:
            return any(value == 1 for value in values)
        if "veraendert sich die bevoelkerung derselben fraktion insgesamt um mindestens 6" in text:
            return any(abs(delta) >= 6 for delta in applied_deltas.values())
        if "mindestens 3 propagandakarten" in text and "mindestens 15 propagandamacht" in text:
            return any(prop_counts.get(faction_id, 0) >= 3 and objective_prop_power.get(faction_id, 0) >= 15 for faction_id in context.state.factions)
        if "mindestens drei erfolgreiche angriffe" in text:
            return sum(1 for record in attack_records if record["impact"] > 0) >= 3
        if "mindestens zwei verschiedene komboarten" in text:
            return any(
                sum([self._has_pair(strengths), self._has_three_of_a_kind(strengths), self._has_straight(strengths)]) >= 2
                for strengths in row_strengths.values()
            )
        if "journalist wird gestuerzt" in text and "mindestens 3 quellen" in text:
            payload = self._latest_payload(round_events, EventType.JOURNALIST_CHECKED)
            return payload.get("changed") is True and int(payload.get("challenger_total") or 0) >= 3
        if "weniger als 80 bevoelkerung im aktiven spiel" in text:
            return active_population < 80
        return False

    def _current_round_events(self, context: PhaseContext) -> list[Any]:
        return [event for event in context.state.event_log.events if event.round == context.state.round.round_number]

    def _latest_payload(self, events: list[Any], event_type: EventType) -> dict[str, Any]:
        for event in reversed(events):
            if event.event_type == event_type:
                return dict(event.payload)
        return {}

    def _round_start_payload(self, events: list[Any]) -> dict[str, Any]:
        return self._latest_payload(events, EventType.ROUND_START_SNAPSHOT)

    def _normalize_condition(self, condition: str) -> str:
        normalized = condition.lower()
        replacements = {
            "ä": "ae",
            "ö": "oe",
            "ü": "ue",
            "ß": "ss",
            "höchstens": "hoechstens",
            "straße": "strasse",
            "gestürzt": "gestuerzt",
        }
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)
        return normalized

    def _research_attack_records(self, row: list[str], total_power: dict[str, int]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for attacker, defender in self._world_history_transitions(row):
            if attacker is None or defender is None or attacker == defender:
                continue
            margin = total_power.get(attacker, 0) - total_power.get(defender, 0)
            records.append(
                {
                    "attacker": attacker,
                    "defender": defender,
                    "margin": margin,
                    "impact": self._combat_impact(margin),
                }
            )
        return records

    def _research_propaganda_counts(self, slots: list[str | None]) -> dict[str, int]:
        counts = {faction_id: 0 for faction_id in self.state.factions}
        for card_id in slots:
            faction_id = self._card_faction(card_id)
            if faction_id in counts:
                counts[faction_id] += 1
        return {faction_id: count for faction_id, count in counts.items() if count > 0}

    def _objective_propaganda_power(self, slots: list[str | None]) -> dict[str, int]:
        slot_factors = self.config.v03.get("propaganda", {}).get("slot_factors", [1 for _ in slots])
        power = {faction_id: 0 for faction_id in self.state.factions}
        for index, card_id in enumerate(slots):
            card = self.cards_by_id.get(card_id or "")
            if card is None or card.faction not in power:
                continue
            factor = int(slot_factors[index]) if index < len(slot_factors) else 1
            power[card.faction] += max(0, int(card.strength)) * factor
        return power

    def _card_faction(self, card_id: Any) -> str | None:
        if card_id is None:
            return None
        card = self.cards_by_id.get(str(card_id))
        return card.faction if card is not None else None

    def _event_payload_sum(self, events: list[Any], key: str) -> int:
        total = 0
        for event in events:
            value = event.payload.get(key)
            if isinstance(value, int):
                total += value
        return total

    def _has_pair(self, strengths: list[int]) -> bool:
        return any(strengths.count(value) >= 2 for value in set(strengths))

    def _has_three_of_a_kind(self, strengths: list[int]) -> bool:
        return any(strengths.count(value) >= 3 for value in set(strengths))

    def _has_straight(self, strengths: list[int]) -> bool:
        unique = sorted(set(strengths))
        return any({value, value + 1, value + 2}.issubset(unique) for value in unique)

    def _check_v03_victory(self, context: PhaseContext) -> None:
        for faction in context.state.factions.values():
            faction.eliminated = faction.population <= 0
        populations = {faction_id: faction.population for faction_id, faction in context.state.factions.items()}
        propaganda_power = self._active_propaganda_power(self._world_history_base_power(context.state.world_history_row))
        results: list[dict[str, str]] = []
        living = [faction_id for faction_id, population in populations.items() if population > 0]
        if not living:
            results.append({"tier": "collapse", "winner_type": "none", "winner_faction": ""})
        elif len(living) == 1:
            results.append({"tier": "hegemony", "winner_type": "faction", "winner_faction": living[0]})
        for faction_id, population in populations.items():
            if population >= int(self.config.v03.get("victory", {}).get("dominance", {}).get("population_threshold", 55)):
                if propaganda_power.get(faction_id, 0) >= int(self.config.v03.get("victory", {}).get("dominance", {}).get("propaganda_power_threshold", 15)):
                    results.append({"tier": "dominance", "winner_type": "faction", "winner_faction": faction_id})
            own_prop_count = sum(
                1
                for card_id in context.state.propaganda_track.get_slots()
                if card_id in self.cards_by_id and self.cards_by_id[card_id].faction == faction_id
            )
            breakthrough = self.config.v03.get("victory", {}).get("breakthrough", {})
            if (
                population >= int(breakthrough.get("population_threshold", 35))
                and own_prop_count >= int(breakthrough.get("own_propaganda_cards_threshold", 3))
                and propaganda_power.get(faction_id, 0) >= int(breakthrough.get("final_power_threshold", 12))
            ):
                results.append({"tier": "breakthrough", "winner_type": "faction", "winner_faction": faction_id})

        order = list(self.config.v03.get("victory", {}).get("order", ["collapse", "hegemony", "dominance", "breakthrough"]))
        ordered_results = sorted(results, key=lambda item: order.index(item["tier"]) if item["tier"] in order else len(order))
        chosen = ordered_results[0] if ordered_results else None
        same_tier = [item for item in ordered_results if chosen is not None and item["tier"] == chosen["tier"]]
        is_win = chosen is not None and len(same_tier) == 1 and chosen["winner_type"] != "none"
        payload = {
            "is_win": is_win,
            "winner_type": chosen["winner_type"] if is_win and chosen else "none",
            "winner_faction": chosen["winner_faction"] if is_win and chosen else None,
            "winner_player": None,
            "winning_condition": chosen["tier"] if is_win and chosen else None,
            "checked_timing": "v0_3_victory_check",
            "tie_info": {"same_tier_results": same_tier} if chosen is not None and len(same_tier) > 1 else None,
            "populations": populations,
            "propaganda_power": propaganda_power,
            "eliminated_factions": [faction_id for faction_id, faction in context.state.factions.items() if faction.eliminated],
            "order": order,
        }
        context.event_bus.emit(EventType.VICTORY_CHECKED, payload)
        if is_win and chosen is not None:
            self.ended_by = "victory"
            self.winner_type = "faction"
            self.winner_faction = chosen["winner_faction"]
            self.winning_condition = chosen["tier"]
            context.event_bus.emit(
                EventType.GAME_ENDED,
                {
                    "ended_by": self.ended_by,
                    "winner_type": self.winner_type,
                    "winner_player": self.winner_player,
                    "winner_faction": self.winner_faction,
                    "winning_condition": self.winning_condition,
                    "tie_info": self.tie_info,
                    "checked_timing": "v0_3_victory_check",
                },
            )

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
            drawn = context.state.deck.draw_cards(
                1,
                config=context.config.deck,
                rng=context.state.rng,
                event_bus=context.event_bus,
                reason="draft",
                player_id=player_id,
                destination="draft_pack",
                game_id=context.config.game_id,
                round_number=context.state.round.round_number,
                phase=context.phase_name,
            )
            if not drawn:
                break
            draft_pack.append(drawn[0])
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

    def _validate_state_if_strict(self) -> None:
        if self.config.quality.strict_mode:
            validate_game_state(self.config, self.state, self.cards)

    def _build_card_lookup(self, cards: list[CardConfig]) -> dict[str, CardConfig]:
        lookup = {card.id: card for card in cards}
        logical_cards = lookup
        for instance_id, instance in self.state.deck.card_instances.items():
            if instance.card_id in logical_cards:
                lookup[instance_id] = logical_cards[instance.card_id]
        return lookup
