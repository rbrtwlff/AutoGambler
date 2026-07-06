from __future__ import annotations

import random

from autogambler.actions import LegalAction
from autogambler.config import BotProfileConfig
from autogambler.state import BotView


class WeightedBot:
    def __init__(self, profile: BotProfileConfig) -> None:
        self.profile = profile

    def choose_action(self, view: BotView, legal_actions: list[LegalAction], rng: random.Random) -> LegalAction:
        scored = [(self._score(view, action, rng), action) for action in legal_actions]
        max_score = max(score for score, _action in scored)
        best = [action for score, action in scored if score == max_score]
        return rng.choice(best)

    def _score(self, view: BotView, action: LegalAction, rng: random.Random) -> float:
        weights = self.profile.weights
        score = 0.0
        if action.target_faction_id == view.own_faction_id:
            score += weights.get("prefer_own_faction", 0.0)
        if action.target_faction_id is not None:
            population = view.public_faction_population[action.target_faction_id]
            highest_population = max(view.public_faction_population.values())
            if population == highest_population and action.target_faction_id != view.own_faction_id:
                score += weights.get("attack_leader", 0.0)
            score += population * weights.get("prefer_high_population_target", 0.0) / 100.0
        score += rng.random() * weights.get("random_noise", 0.0)
        return score

