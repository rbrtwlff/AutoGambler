from __future__ import annotations

from pathlib import Path

from wsim.bots import BotFactory, DeceptiveBot, LegalActionProvider, LoyalistBot, SaboteurBot
from wsim.core.models import BotConfig
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


def test_bot_factory_loads_heuristic_bots() -> None:
    rules, _cards, bot_configs, _state, _provider = _fixture()
    bots = BotFactory().create_all(bot_configs)

    assert set(bots) == {player.id for player in rules.players}
    assert isinstance(bots["P1"], LoyalistBot)
    assert isinstance(bots["P2"], DeceptiveBot)
    assert isinstance(bots["P3"], LoyalistBot)
    assert isinstance(bots["P4"], SaboteurBot)


def test_heuristic_bot_creates_legal_decisions() -> None:
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


def test_heuristic_bot_decisions_include_reason() -> None:
    _rules, _cards, bot_configs, state, provider = _fixture()
    bot = BotFactory().create(bot_configs[0])
    context = provider.build_context(state, "P1")
    options = provider.target_faction_options(context)

    decision = bot.choose_target_faction(context, options, state.rng)

    assert decision.reason
    assert [item["option"] for item in decision.considered_options] == options


def test_heuristic_bot_supports_own_secret_faction() -> None:
    _rules, _cards, bot_configs, state, provider = _fixture()
    bot_configs[0].randomness = 0
    bot = BotFactory().create(bot_configs[0])
    context = provider.build_context(state, "P1")
    secret = context.public_view["players"]["P1"]["secret_faction_id"]

    decision = bot.choose_target_faction(context, provider.target_faction_options(context), state.rng)

    assert decision.choice == secret
    assert "secret faction" in decision.reason


def test_heuristic_bot_often_attacks_leading_opponent() -> None:
    _rules, _cards, bot_configs, state, provider = _fixture()
    bot_configs[0].randomness = 0
    state.players["P1"].secret_faction_id = "red"
    state.factions["red"].population = 4
    state.factions["black"].population = 20
    state.factions["yellow"].population = 8
    state.factions["green"].population = 7
    bot = BotFactory().create(bot_configs[0])
    context = provider.build_context(state, "P1")

    attack_decision = bot.choose_action_type(context, provider.action_type_options(context), state.rng)
    target_decision = bot.choose_target_faction(context, provider.target_faction_options(context), state.rng)

    assert attack_decision.choice == "attack"
    assert target_decision.choice == "black"


def test_heuristic_randomness_changes_decisions_with_different_seed() -> None:
    _rules, _cards, bot_configs, state, provider = _fixture()
    bot_configs[0].randomness = 1.0
    bot = BotFactory().create(bot_configs[0])
    context = provider.build_context(state, "P1")
    options = provider.cards_to_commit_options(context)

    choices = {
        str(bot.choose_cards_to_commit(context, options, state.rng).choice)
        for state in [create_initial_state(_rules, _cards, seed=seed) for seed in range(10, 30)]
        for context in [provider.build_context(state, "P1")]
    }

    assert len(choices) > 1


def test_heuristic_reason_is_logged_on_action_commit() -> None:
    rules, cards, bot_configs, _state, _provider = _fixture()
    from wsim.engine import GameEngine

    engine = GameEngine(rules, cards, seed=123, bots=bot_configs)
    engine.run_round()

    committed = [
        event for event in engine.state.export_events_as_dicts() if event["event_type"] == "action_committed"
    ]
    assert committed
    assert committed[0]["payload"]["action_type_reason"]
    assert committed[0]["payload"]["target_faction_reason"]


def test_saboteur_bot_causes_more_average_population_damage_than_loyalist() -> None:
    rules, cards, _bot_configs, _state, provider = _fixture()
    saboteur = BotFactory().create(
        BotConfig(id="sab", player_id="P1", type="saboteur", aggression=0.9, randomness=0.05)
    )
    loyalist = BotFactory().create(
        BotConfig(id="loy", player_id="P1", type="loyalist", aggression=0.45, randomness=0.05)
    )

    saboteur_attacks = 0
    loyalist_attacks = 0
    for seed in range(20):
        state = create_initial_state(rules, cards, seed=seed)
        state.players["P1"].secret_faction_id = "red"
        state.factions["red"].population = 5
        state.factions["black"].population = 14
        context = provider.build_context(state, "P1")
        saboteur_attacks += saboteur.choose_action_type(context, ["support", "attack"], state.rng).choice == "attack"

        state = create_initial_state(rules, cards, seed=seed)
        state.players["P1"].secret_faction_id = "red"
        state.factions["red"].population = 5
        state.factions["black"].population = 14
        context = provider.build_context(state, "P1")
        loyalist_attacks += loyalist.choose_action_type(context, ["support", "attack"], state.rng).choice == "attack"

    assert saboteur_attacks > loyalist_attacks


def test_deceptive_bot_supports_own_faction_less_obviously_than_loyalist() -> None:
    rules, cards, _bot_configs, _state, provider = _fixture()
    deceptive = BotFactory().create(BotConfig(id="dec", player_id="P1", type="deceptive", randomness=0))
    loyalist = BotFactory().create(BotConfig(id="loy", player_id="P1", type="loyalist", randomness=0))
    state = create_initial_state(rules, cards, seed=123)
    state.players["P1"].secret_faction_id = "red"
    context = provider.build_context(state, "P1")

    deceptive_decision = deceptive.choose_target_faction(context, provider.target_faction_options(context), state.rng)
    loyalist_decision = loyalist.choose_target_faction(context, provider.target_faction_options(context), state.rng)

    assert loyalist_decision.choice == "red"
    assert deceptive_decision.score < loyalist_decision.score


def test_loyalist_bot_has_higher_directness_values() -> None:
    loyalist = BotFactory().create(BotConfig(id="loy", player_id="P1", type="loyalist"))
    deceptive = BotFactory().create(BotConfig(id="dec", player_id="P1", type="deceptive"))
    saboteur = BotFactory().create(BotConfig(id="sab", player_id="P1", type="saboteur"))

    assert loyalist.directness > deceptive.directness
    assert loyalist.directness > saboteur.directness


def test_specialized_bots_produce_only_legal_actions() -> None:
    _rules, _cards, _bot_configs, state, provider = _fixture()
    configs = [
        BotConfig(id="loy", player_id="P1", type="loyalist"),
        BotConfig(id="dec", player_id="P1", type="deceptive"),
        BotConfig(id="sab", player_id="P1", type="saboteur"),
    ]
    context = provider.build_context(state, "P1")
    all_options = provider.all_options(context)

    for config in configs:
        bot = BotFactory().create(config)
        for method_name, options in all_options.items():
            decision = getattr(bot, method_name)(context, options, state.rng)
            assert decision.choice in options
