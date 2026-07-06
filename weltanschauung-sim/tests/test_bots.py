from __future__ import annotations

from pathlib import Path

from wsim.bots import BotFactory, LegalActionProvider, RandomBot
from wsim.config import load_bots_config, load_cards_config, load_rules_config
from wsim.engine import create_initial_state


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")
BASE_BOTS = Path("configs/bots/bot_profiles.yaml")


def _fixture():
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    bots = load_bots_config(BASE_BOTS, rules_config=rules)
    state = create_initial_state(rules, cards, seed=123)
    provider = LegalActionProvider(rules, cards)
    return rules, cards, bots, state, provider


def test_bot_factory_loads_random_bots() -> None:
    rules, _cards, bot_configs, _state, _provider = _fixture()
    bots = BotFactory().create_all(bot_configs)

    assert set(bots) == {player.id for player in rules.players}
    assert all(isinstance(bot, RandomBot) for bot in bots.values())


def test_random_bot_creates_legal_decisions() -> None:
    _rules, _cards, bot_configs, state, provider = _fixture()
    bot = BotFactory().create(bot_configs[0])
    context = provider.build_context(state, "P1")
    all_options = provider.all_options(context)

    for method_name, options in all_options.items():
        decision = getattr(bot, method_name)(context, options, state.rng)
        assert decision.choice in options


def test_bot_context_contains_no_other_players_secret_information() -> None:
    _rules, _cards, _bot_configs, state, provider = _fixture()
    context = provider.build_context(state, "P1")

    assert "secret_faction_id" in context.public_view["players"]["P1"]
    assert "hidden_research_orders" in context.public_view["players"]["P1"]
    for player_id in ["P2", "P3", "P4"]:
        assert "secret_faction_id" not in context.public_view["players"][player_id]
        assert "hidden_research_orders" not in context.public_view["players"][player_id]
        assert "hand" not in context.public_view["players"][player_id]


def test_random_bot_decisions_include_reason() -> None:
    _rules, _cards, bot_configs, state, provider = _fixture()
    bot = BotFactory().create(bot_configs[0])
    context = provider.build_context(state, "P1")
    options = provider.target_faction_options(context)

    decision = bot.choose_target_faction(context, options, state.rng)

    assert decision.reason
    assert decision.considered_options == options

