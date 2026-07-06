from __future__ import annotations

import random
from dataclasses import dataclass

from autogambler.actions import legal_actions_for_view
from autogambler.bots import WeightedBot
from autogambler.config import ConfigBundle, ConfigError
from autogambler.events import EventLog
from autogambler.state import BotView, GameState, PlannedAction, PlayerState


@dataclass(frozen=True)
class SimulationResult:
    game_index: int
    seed: int
    winner_faction_id: str | None
    victory_condition_id: str | None
    rounds_played: int
    events: list[dict]
    final_population: dict[str, int]


class SimulationEngine:
    def __init__(self, config: ConfigBundle) -> None:
        self.config = config
        self.action_types = {action.id: action for action in config.game.action_types}
        self.bot_profiles = {profile.id: profile for profile in config.bots.profiles}

    def run_game(self, game_index: int, seed: int) -> SimulationResult:
        rng = random.Random(seed)
        events = EventLog()
        state = self._initial_state(rng, events)
        for round_number in range(1, self.config.game.rounds.max_rounds + 1):
            state.round_number = round_number
            for phase in self.config.game.rounds.phases:
                state.phase = phase
                events.record("phase_started", round_number, phase)
                handler = getattr(self, f"_phase_{phase}", None)
                if handler is None:
                    raise ConfigError(f"No engine handler for configured phase {phase!r}.")
                handler(state, events, rng)
                self._check_victory(state, events)
                if state.winner_faction_id is not None:
                    return self._result(game_index, seed, state, events)
        self._apply_round_limit_victory(state, events)
        return self._result(game_index, seed, state, events)

    def _initial_state(self, rng: random.Random, events: EventLog) -> GameState:
        faction_population = {faction.id: faction.start_population for faction in self.config.game.factions}
        players = {
            player.id: PlayerState(
                id=player.id,
                faction_id=player.faction_id,
                bot_profile_id=player.bot_profile_id,
            )
            for player in self.config.game.players
        }
        deck = [card.id for card in self.config.cards.cards]
        rng.shuffle(deck)
        state = GameState(
            round_number=0,
            phase="setup",
            faction_population=faction_population,
            neutral_population=self.config.game.population.neutral,
            players=players,
            deck=deck,
        )
        events.record(
            "game_started",
            0,
            "setup",
            faction_population=dict(state.faction_population),
            neutral_population=state.neutral_population,
        )
        self._deal_starting_hands(state, events)
        return state

    def _deal_starting_hands(self, state: GameState, events: EventLog) -> None:
        for player in state.players.values():
            for _ in range(self.config.game.limits.starting_hand_size):
                card_id = self._draw_card(state)
                if card_id is not None:
                    player.hand.append(card_id)
                    events.record("card_drawn", state.round_number, state.phase, player_id=player.id, card_id=card_id)

    def _phase_draft(self, state: GameState, events: EventLog, rng: random.Random) -> None:
        for player in state.players.values():
            for _ in range(self.config.game.limits.draft_pick_count):
                card_id = self._draw_card(state)
                if card_id is None:
                    return
                player.hand.append(card_id)
                events.record("draft_pick", state.round_number, state.phase, player_id=player.id, card_id=card_id)

    def _phase_planning(self, state: GameState, events: EventLog, rng: random.Random) -> None:
        state.planned_actions.clear()
        for player in state.players.values():
            view = self._view_for_player(state, player.id)
            legal_actions = legal_actions_for_view(self.config, view)
            bot = WeightedBot(self.bot_profiles[player.bot_profile_id])
            action = bot.choose_action(view, legal_actions, rng).to_planned_action(player.id)
            self._assert_legal_planned_action(view, action)
            state.planned_actions.append(action)
            events.record(
                "action_planned",
                state.round_number,
                state.phase,
                player_id=player.id,
                action_type_id=action.action_type_id,
                target_faction_id=action.target_faction_id,
                card_id=action.card_id,
            )

    def _phase_resolution(self, state: GameState, events: EventLog, rng: random.Random) -> None:
        for action in list(state.planned_actions):
            action_type = self.action_types[action.action_type_id]
            if action.target_faction_id is None:
                continue
            before = state.faction_population[action.target_faction_id]
            after = max(0, before + action_type.population_delta)
            actual_delta = after - before
            state.faction_population[action.target_faction_id] = after
            state.neutral_population -= actual_delta
            player = state.players[action.player_id]
            if action.card_id in player.hand:
                player.hand.remove(action.card_id)
                state.discard.append(action.card_id)
            events.record(
                "population_changed",
                state.round_number,
                state.phase,
                player_id=action.player_id,
                action_type_id=action.action_type_id,
                target_faction_id=action.target_faction_id,
                before=before,
                after=after,
                neutral_population=state.neutral_population,
            )

    def _phase_upkeep(self, state: GameState, events: EventLog, rng: random.Random) -> None:
        state.planned_actions.clear()
        events.record(
            "upkeep_completed",
            state.round_number,
            state.phase,
            faction_population=dict(state.faction_population),
            neutral_population=state.neutral_population,
        )

    def _draw_card(self, state: GameState) -> str | None:
        if not state.deck:
            return None
        return state.deck.pop()

    def _view_for_player(self, state: GameState, player_id: str) -> BotView:
        player = state.players[player_id]
        return BotView(
            player_id=player.id,
            own_faction_id=player.faction_id,
            own_hand=tuple(player.hand),
            own_sources=tuple(player.sources),
            public_faction_population=dict(state.faction_population),
            neutral_population=state.neutral_population,
            round_number=state.round_number,
            phase=state.phase,
        )

    def _assert_legal_planned_action(self, view: BotView, action: PlannedAction) -> None:
        legal_actions = legal_actions_for_view(self.config, view)
        legal_tuples = {(item.action_type_id, item.target_faction_id, item.card_id) for item in legal_actions}
        planned_tuple = (action.action_type_id, action.target_faction_id, action.card_id)
        if planned_tuple not in legal_tuples:
            raise ConfigError(f"Bot generated illegal action: {planned_tuple}")

    def _check_victory(self, state: GameState, events: EventLog) -> None:
        if state.winner_faction_id is not None:
            return
        for condition in self.config.game.victory_conditions:
            if condition.type != "faction_population_at_least":
                continue
            assert condition.threshold is not None
            winners = [
                faction_id
                for faction_id, population in state.faction_population.items()
                if population >= condition.threshold
            ]
            if winners:
                state.winner_faction_id = sorted(winners)[0]
                state.victory_condition_id = condition.id
                events.record(
                    "victory_reached",
                    state.round_number,
                    state.phase,
                    winner_faction_id=state.winner_faction_id,
                    victory_condition_id=condition.id,
                )
                return

    def _apply_round_limit_victory(self, state: GameState, events: EventLog) -> None:
        for condition in self.config.game.victory_conditions:
            if condition.type == "highest_population_at_round_limit":
                highest = max(state.faction_population.values())
                winners = [key for key, value in state.faction_population.items() if value == highest]
                state.winner_faction_id = sorted(winners)[0]
                state.victory_condition_id = condition.id
                events.record(
                    "victory_reached",
                    state.round_number,
                    state.phase,
                    winner_faction_id=state.winner_faction_id,
                    victory_condition_id=condition.id,
                )
                return
        raise ConfigError("No round-limit victory condition configured.")

    def _result(self, game_index: int, seed: int, state: GameState, events: EventLog) -> SimulationResult:
        if state.population_total() != self.config.game.population.total:
            raise ConfigError("Population total changed; add explicit rules for births/losses before allowing this.")
        return SimulationResult(
            game_index=game_index,
            seed=seed,
            winner_faction_id=state.winner_faction_id,
            victory_condition_id=state.victory_condition_id,
            rounds_played=state.round_number,
            events=[event.to_dict() for event in events.events],
            final_population=dict(state.faction_population),
        )

