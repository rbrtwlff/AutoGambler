from __future__ import annotations

from pathlib import Path

from wsim.bots import BotFactory, LegalActionProvider
from wsim.core.models import BotConfig
from wsim.config import load_cards_config, load_rules_config
from wsim.engine import create_initial_state


RULES_V0_3 = Path("configs/rules/rules_v0_3.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _fixture(seed: int = 123):
    rules = load_rules_config(RULES_V0_3)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    state = create_initial_state(rules, cards, seed=seed)
    provider = LegalActionProvider(rules, cards)
    return rules, cards, state, provider


def test_every_v03_bot_decision_point_returns_legal_choice() -> None:
    _rules, _cards, state, provider = _fixture()
    state.players["P1"].sources = ["source_token_1", "source_token_2"]
    context = provider.build_context(state, "P1")
    options_by_method = {
        "how_many_sources_to_bid": provider.source_bid_options(context),
        "which_player_to_vote_for": provider.player_vote_options(context),
        "choose_card_to_contribute_to_draft": provider.draft_contribution_options(context),
        "choose_card_to_take_from_three": state.players["P1"].hand[:3],
        "choose_future_set_or_remove_propaganda": provider.journalist_v03_action_options(context),
        "choose_card_to_put_on_top_of_draw_deck": state.players["P1"].hand[:3],
        "choose_propaganda_to_remove": provider.propaganda_to_remove_options(context) or [None],
        "choose_pool_card_to_discard_as_cost": state.players["P1"].hand[:3],
        "choose_one_of_two_as_new_propaganda": state.players["P1"].hand[:2],
        "choose_number_of_cards_for_urn": provider.urn_count_options(context),
        "choose_cards_for_urn": provider.urn_card_options(context, count=1),
        "choose_completed_research_assignment_to_score": provider.research_order_priority_options(context),
        "choose_whether_to_discard_research_assignment": [True, False],
        "choose_research_assignment_to_discard": provider.research_assignment_discard_options(context),
    }
    bot_configs = [
        BotConfig(id="random", player_id="P1", type="random"),
        BotConfig(id="heuristic", player_id="P1", type="heuristic", randomness=0),
        BotConfig(id="loyalist", player_id="P1", type="loyalist", randomness=0),
        BotConfig(id="deceptive", player_id="P1", type="deceptive", randomness=0),
        BotConfig(id="saboteur", player_id="P1", type="saboteur", randomness=0),
    ]

    for bot_config in bot_configs:
        bot = BotFactory().create(bot_config)
        for method_name, options in options_by_method.items():
            decision = getattr(bot, method_name)(context, options, state.rng)
            assert decision.choice in options
            assert decision.reason


def test_v03_bot_context_hides_forbidden_information() -> None:
    _rules, _cards, state, provider = _fixture()
    context = provider.build_context(state, "P1")

    assert "secret_faction_id" in context.public_view["players"]["P1"]
    assert "hand" in context.public_view["players"]["P1"]
    assert "hidden_research_orders" in context.public_view["players"]["P1"]
    for player_id in ["P2", "P3", "P4"]:
        other = context.public_view["players"][player_id]
        assert "secret_faction_id" not in other
        assert "hand" not in other
        assert "hidden_research_orders" not in other


def test_heuristic_bot_puts_own_faction_into_urn_more_than_random() -> None:
    rules, cards, _state, provider = _fixture()
    heuristic = BotFactory().create(BotConfig(id="h", player_id="P1", type="heuristic", randomness=0))
    random_bot = BotFactory().create(BotConfig(id="r", player_id="P1", type="random"))
    heuristic_own = 0
    random_own = 0

    for seed in range(30):
        state = create_initial_state(rules, cards, seed=seed)
        state.players["P1"].secret_faction_id = "red"
        state.players["P1"].hand = ["red_action_03", "red_action_04", "black_action_03", "green_action_03"]
        context = provider.build_context(state, "P1")
        options = provider.urn_card_options(context, count=2)
        heuristic_choice = heuristic.choose_cards_for_urn(context, options, state.rng).choice
        heuristic_own += sum(1 for card_id in heuristic_choice if context.cards_by_id[card_id].faction == "red")

        state = create_initial_state(rules, cards, seed=seed)
        state.players["P1"].secret_faction_id = "red"
        state.players["P1"].hand = ["red_action_03", "red_action_04", "black_action_03", "green_action_03"]
        context = provider.build_context(state, "P1")
        random_choice = random_bot.choose_cards_for_urn(context, options, state.rng).choice
        random_own += sum(1 for card_id in random_choice if context.cards_by_id[card_id].faction == "red")

    assert heuristic_own > random_own


def test_v03_media_mogul_decision_prefers_useful_own_propaganda_more_than_random() -> None:
    rules, cards, _state, provider = _fixture()
    heuristic = BotFactory().create(BotConfig(id="h", player_id="P1", type="heuristic", randomness=0))
    random_bot = BotFactory().create(BotConfig(id="r", player_id="P1", type="random"))
    heuristic_own = 0
    random_own = 0
    options = ["red_hybrid_04", "black_action_01"]

    for seed in range(40):
        state = create_initial_state(rules, cards, seed=seed)
        state.players["P1"].secret_faction_id = "red"
        context = provider.build_context(state, "P1")
        heuristic_own += heuristic.choose_one_of_two_as_new_propaganda(context, options, state.rng).choice == "red_hybrid_04"

        state = create_initial_state(rules, cards, seed=seed)
        state.players["P1"].secret_faction_id = "red"
        context = provider.build_context(state, "P1")
        random_own += random_bot.choose_one_of_two_as_new_propaganda(context, options, state.rng).choice == "red_hybrid_04"

    assert heuristic_own > random_own


def test_v03_source_bids_never_exceed_source_count() -> None:
    _rules, _cards, state, provider = _fixture()
    state.players["P1"].sources = ["s1", "s2"]
    context = provider.build_context(state, "P1")
    options = provider.source_bid_options(context)
    bot = BotFactory().create(BotConfig(id="h", player_id="P1", type="heuristic"))

    for _ in range(20):
        decision = bot.how_many_sources_to_bid(context, options, state.rng)
        assert 0 <= decision.choice <= 2


def test_v03_urn_card_count_is_legal() -> None:
    _rules, _cards, state, provider = _fixture()
    state.players["P1"].hand = ["red_action_03", "black_action_03"]
    context = provider.build_context(state, "P1")
    options = provider.urn_count_options(context)
    bot = BotFactory().create(BotConfig(id="h", player_id="P1", type="heuristic"))

    decision = bot.choose_number_of_cards_for_urn(context, options, state.rng)

    assert decision.choice in [1, 2]
