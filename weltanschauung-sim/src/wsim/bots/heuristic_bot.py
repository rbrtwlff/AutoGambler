from __future__ import annotations

from typing import Any

from wsim.bots.base import BaseBot, BotContext, BotDecision
from wsim.core.models import CardConfig
from wsim.core.state import GameRng


class HeuristicBot(BaseBot):
    profile_name = "heuristic"
    directness = 0.6
    deception = 0.35
    sabotage = 0.0

    def choose_draft_pick(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("draft pick", context, options, rng, self._score_card)

    def choose_draft_pass(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("draft pass", context, options, rng, lambda _context, option: 0.1 if option else 0.0)

    def choose_journalist_action(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored(
            "journalist action",
            context,
            options,
            rng,
            lambda _context, option: 1.0 if option == "observe" else 0.4,
        )

    def choose_media_mogul_card(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("media mogul card", context, options, rng, self._score_media_card)

    def choose_cards_to_commit(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("cards to commit", context, options, rng, self._score_commit_option)

    def choose_action_type(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("action type", context, options, rng, self._score_action_type)

    def choose_acting_faction(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        secret = self._secret_faction(context)
        return self._choose_scored(
            "acting faction",
            context,
            options,
            rng,
            lambda _context, option: 2.0 if option == secret else 0.7,
        )

    def choose_target_faction(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("target faction", context, options, rng, self._score_target_faction)

    def choose_source_use(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("source use", context, options, rng, lambda _context, option: 0.0 if option is None else 0.8)

    def choose_research_order_priority(self, context: BotContext, options: list[Any], rng: GameRng) -> BotDecision:
        return self._choose_scored("research order priority", context, options, rng, self._score_card)

    def _choose_scored(self, label: str, context: BotContext, options: list[Any], rng: GameRng, scorer) -> BotDecision:
        if not options:
            return BotDecision(choice=None, reason=f"{self._bot_name()} had no legal options for {label}.", considered_options=[])

        scored = [(option, float(scorer(context, option))) for option in options]
        scored = [(option, score + self._noise(rng)) for option, score in scored]
        best_option, best_score = max(scored, key=lambda item: item[1])
        return BotDecision(
            choice=best_option,
            reason=self._reason(label, best_option, best_score),
            score=best_score,
            considered_options=[{"option": option, "score": score} for option, score in scored],
        )

    def _score_card(self, context: BotContext, card_id: Any) -> float:
        card = context.cards_by_id.get(str(card_id))
        if card is None:
            return 0.0
        score = float(card.strength)
        secret = self._secret_faction(context)
        if self._card_matches_secret(card, secret):
            score += (2.0 + self.directness) * self.config.skill + self._weight("secret_faction_card", 1.0)
        elif self.deception > 0:
            score += 0.25 * self.deception
        if any(tag in card.tags for tag in self._propaganda_tags(context)):
            score += self.config.propaganda_awareness
        if card.action_effects or card.propaganda_effects:
            score += 0.8
        return score

    def _score_media_card(self, context: BotContext, card_id: Any) -> float:
        card = context.cards_by_id.get(str(card_id))
        if card is None:
            return 0.0
        score = self._score_card(context, card_id)
        if card.type in {"propaganda", "hybrid"}:
            score += 1.0 + self.config.propaganda_awareness
        return score

    def _score_commit_option(self, context: BotContext, option: Any) -> float:
        card_ids = list(option)
        if not card_ids:
            return 0.35 + (1 - self.config.risk_tolerance) + self.deception * 0.2
        score = sum(self._score_card(context, card_id) for card_id in card_ids)
        score += len(card_ids) * (self.config.risk_tolerance - 0.25)
        if self._is_endgame(context):
            score += len(card_ids) * 0.8
        return score

    def _score_action_type(self, context: BotContext, option: Any) -> float:
        secret = self._secret_faction(context)
        populations = self._populations(context)
        leader = self._leader_faction(context)
        own_pop = populations.get(secret, 0) if secret else 0
        leader_pop = populations.get(leader, 0) if leader else 0
        if option == "support":
            score = 1.2 + self.config.secrecy + self.directness * 0.4
            if own_pop < leader_pop:
                score += 0.7
            if self._is_endgame(context):
                score += 1.0
            if self.sabotage:
                score -= 1.2 * self.sabotage
            return score
        if option == "attack":
            score = 0.8 + self.config.aggression
            if leader and leader != secret:
                score += 1.2 + (leader_pop - own_pop) * 0.08
            if self._is_endgame(context):
                score += self.config.aggression
            if self.sabotage:
                score += 1.5 * self.sabotage
            return score
        return 0.0

    def _score_target_faction(self, context: BotContext, option: Any) -> float:
        faction_id = str(option)
        secret = self._secret_faction(context)
        leader = self._leader_faction(context)
        populations = self._populations(context)
        score = populations.get(faction_id, 0) * 0.05
        if self.sabotage:
            if faction_id == leader:
                score += 2.0 + self.config.aggression
            score += (populations.get(faction_id, 0) <= 3) * -1.0
            if faction_id == secret and self.deception:
                score += 0.4 * self.deception
            return score
        if faction_id == secret:
            score += 1.8 + self.config.secrecy + self.directness
            if self._is_endgame(context):
                score += 1.2
        elif faction_id == leader:
            score += 1.4 + self.config.aggression * 2
        elif self.deception:
            score += 0.45 * self.deception
        return score

    def _secret_faction(self, context: BotContext) -> str | None:
        player = context.public_view["players"][context.player_id]
        return player.get("secret_faction_id") or player.get("public_faction_id")

    def _populations(self, context: BotContext) -> dict[str, int]:
        return {key: int(value["population"]) for key, value in context.public_view["factions"].items()}

    def _leader_faction(self, context: BotContext) -> str | None:
        populations = self._populations(context)
        if not populations:
            return None
        secret = self._secret_faction(context)
        ordered = sorted(populations.items(), key=lambda item: (item[1], item[0]), reverse=True)
        for faction_id, _population in ordered:
            if faction_id != secret:
                return faction_id
        return ordered[0][0]

    def _card_matches_secret(self, card: CardConfig, secret: str | None) -> bool:
        return secret is not None and (card.faction == secret or secret in card.tags)

    def _propaganda_tags(self, context: BotContext) -> set[str]:
        tags: set[str] = set()
        for card_id in context.public_view["propaganda_track"]["slots"]:
            card = context.cards_by_id.get(card_id) if card_id is not None else None
            if card is not None:
                tags.update(card.tags)
        return tags

    def _is_endgame(self, context: BotContext) -> bool:
        round_number = int(context.public_view["round"]["round_number"])
        max_rounds = context.rules.round_flow.max_rounds
        return round_number >= max(1, int(max_rounds * 0.75))

    def _noise(self, rng: GameRng) -> float:
        return (rng.random_float() - 0.5) * 2 * self.config.randomness

    def _weight(self, name: str, default: float) -> float:
        return float(self.config.weights.get(name, default))

    def _reason(self, label: str, choice: Any, score: float) -> str:
        return (
            f"{self._bot_name()} chose {choice!r} for {label} with score {score:.2f}; "
            f"profile={self.profile_name}, directness={self.directness:.2f}, deception={self.deception:.2f}, "
            "using public state, own secret faction, own hidden info, card synergy, and controlled randomness."
        )

    def _bot_name(self) -> str:
        return self.__class__.__name__


class LoyalistBot(HeuristicBot):
    profile_name = "loyalist"
    directness = 1.0
    deception = 0.05
    sabotage = 0.0

    def _score_action_type(self, context: BotContext, option: Any) -> float:
        score = super()._score_action_type(context, option)
        if option == "support":
            score += 0.9
        if option == "attack":
            score += 0.35
        return score


class DeceptiveBot(HeuristicBot):
    profile_name = "deceptive"
    directness = 0.45
    deception = 0.9
    sabotage = 0.0

    def _score_action_type(self, context: BotContext, option: Any) -> float:
        score = super()._score_action_type(context, option)
        if not self._is_endgame(context):
            if option == "support":
                score -= 0.45
            if option == "attack":
                score += 0.35
        return score

    def _score_target_faction(self, context: BotContext, option: Any) -> float:
        score = super()._score_target_faction(context, option)
        if option == self._secret_faction(context) and not self._is_endgame(context):
            score -= 1.1
        return score


class SaboteurBot(HeuristicBot):
    profile_name = "saboteur"
    directness = 0.35
    deception = 0.65
    sabotage = 1.0

    def _secret_faction(self, context: BotContext) -> str | None:
        return context.public_view["players"][context.player_id].get("public_faction_id")

    def _score_card(self, context: BotContext, card_id: Any) -> float:
        card = context.cards_by_id.get(str(card_id))
        if card is None:
            return 0.0
        score = float(card.strength) * (1.0 + self.config.aggression)
        if card.type in {"propaganda", "hybrid"}:
            score += self.config.propaganda_awareness
        if card.action_effects or card.propaganda_effects:
            score += 0.5
        return score

    def _score_action_type(self, context: BotContext, option: Any) -> float:
        if option == "attack":
            return 3.0 + self.config.aggression + self.config.risk_tolerance
        if option == "support":
            return 0.6 + self.config.secrecy * 0.35
        return 0.0
