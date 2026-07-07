from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Literal

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
        for unchosen in list(context.state.media_mogul_pool):
            context.state.deck.discard_card(unchosen, event_bus=context.event_bus, reason="v03_media_mogul_unchosen", player_id=media_mogul_id)
        context.state.media_mogul_pool.clear()
        context.event_bus.emit(EventType.PROPAGANDA_PLACED, {"player_id": media_mogul_id, "card_id": chosen, "reason": media_decision.reason, "slots": context.state.propaganda_track.get_slots()})
        context.event_bus.emit(EventType.MEDIA_MOGUL_ACTION_TAKEN, {"player_id": media_mogul_id, "card_id": chosen, "reason": media_decision.reason})

    def _phase_discussion(self, context: PhaseContext) -> None:
        context.event_bus.emit(EventType.DISCUSSION_HELD, {"mode": "noop", "message": "Discussion/Intrigue is a v0.3 no-op placeholder."})

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
        total_power = {
            faction_id: base_power.get(faction_id, 0) + propaganda_power.get(faction_id, 0)
            for faction_id in context.state.factions
        }
        context.event_bus.emit(
            EventType.WORLD_HISTORY_POWER_CALCULATED,
            {"base_power": base_power, "propaganda_power": propaganda_power, "total_power": total_power},
        )
        deltas = {faction_id: 0 for faction_id in context.state.factions}
        for attacker, defender in self._world_history_transitions(row):
            if attacker is None:
                if defender is not None and total_power.get(defender, 0) > 0:
                    deltas[defender] += 2 if total_power[defender] >= 12 else 1
                continue
            if defender is None or attacker == defender:
                continue
            margin = total_power.get(attacker, 0) - total_power.get(defender, 0)
            if margin <= 0:
                continue
            impact = self._combat_impact(margin)
            deltas[defender] -= impact
            deltas["neutral"] = deltas.get("neutral", 0) + impact

        before = {faction_id: faction.population for faction_id, faction in context.state.factions.items()}
        neutral_before = context.state.neutral_population
        applied: dict[str, int] = {}
        for faction_id, delta in deltas.items():
            if faction_id == "neutral" or delta == 0:
                continue
            faction = context.state.factions[faction_id]
            if delta < 0:
                actual = -min(faction.population, abs(delta))
                faction.population += actual
                context.state.neutral_population -= actual
                applied[faction_id] = actual
            else:
                actual = min(context.state.neutral_population, delta)
                faction.population += actual
                context.state.neutral_population -= actual
                applied[faction_id] = actual
        context.event_bus.emit(
            EventType.COMBAT_RESOLVED,
            {
                "base_power": base_power,
                "propaganda_power": propaganda_power,
                "total_power": total_power,
                "requested_deltas": deltas,
                "applied_deltas": applied,
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
        completed_count = 0
        discarded_count = 0
        for player_id in self._ordered_player_ids_from_start():
            player = context.state.players[player_id]
            if player.hidden_research_orders:
                bot_context = self.legal_actions.build_context(context.state, player_id)
                decision = self.bots[player_id].choose_completed_research_assignment_to_score(
                    bot_context,
                    list(player.hidden_research_orders),
                    context.state.rng,
                )
                selected = str(decision.choice) if decision.choice in player.hidden_research_orders else player.hidden_research_orders[0]
                if self._research_order_is_fulfilled(selected, context):
                    player.hidden_research_orders.remove(selected)
                    context.state.deck.discard_card(selected, event_bus=context.event_bus, reason="v03_research_order_completed", player_id=player_id)
                    if len(player.sources) < player.max_sources:
                        player.sources.append(f"source_token_{context.state.round.round_number}_{player_id}_{len(player.sources) + 1}")
                    completed_count += 1
                    context.event_bus.emit(
                        EventType.RESEARCH_ORDER_COMPLETED,
                        {"player_id": player_id, "card_id": selected, "source_reward": 1, "reason": decision.reason},
                    )
                else:
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
                            "reason": "v0.3 TODO fulfillment stub allowed redraw.",
                        },
                    )
            while len(player.hidden_research_orders) < 2 and context.state.deck.research_order_pool:
                drawn = context.state.deck.research_order_pool.pop(0)
                player.hidden_research_orders.append(drawn)
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
            {"completed_count": completed_count, "discarded_count": discarded_count, "todo": "Concrete assignment fulfillment logic is not implemented yet."},
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

    def _world_history_transitions(self, row: list[str]) -> list[tuple[str | None, str | None]]:
        transitions: list[tuple[str | None, str | None]] = []
        factions = [self.cards_by_id[card_id].faction if card_id in self.cards_by_id else None for card_id in row]
        if len(factions) == 1:
            transitions.append((None, factions[0]))
        for left, right in zip(factions, factions[1:]):
            transitions.append((left, right))
        return transitions

    def _combat_impact(self, margin: int) -> int:
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
        context.event_bus.emit(
            EventType.RESEARCH_ASSIGNMENTS_CHECKED,
            {
                "card_id": card_id,
                "fulfilled": False,
                "todo": "Concrete v0.3 research assignment fulfillment logic is not implemented yet.",
            },
        )
        return False

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
