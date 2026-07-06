from pathlib import Path

from wsim.bots import BotFactory, LegalActionProvider
from wsim.config import load_cards_config, load_rules_config
from wsim.core.models import BotConfig
from wsim.engine import GameEngine, create_initial_state


BASE_RULES = Path("configs/rules/base_rules.yaml")
BASE_CARDS = Path("configs/cards/base_cards.yaml")


def _fixture():
    rules = load_rules_config(BASE_RULES)
    cards = load_cards_config(BASE_CARDS, rules_config=rules)
    provider = LegalActionProvider(rules, cards)
    return rules, cards, provider


def test_journalist_removes_harmful_propaganda_more_often_than_random() -> None:
    rules, cards, provider = _fixture()
    state = create_initial_state(rules, cards, seed=123)
    state.players["P1"].secret_faction_id = "red"
    state.factions["black"].population = 20
    state.propaganda_track.slots = ["propaganda_01", "black_hybrid_02", None]
    context = provider.build_context(state, "P1")
    bot = BotFactory().create(BotConfig(id="loyalist", player_id="P1", type="loyalist", randomness=0))

    decision = bot.choose_journalist_action(context, provider.journalist_action_options(context), state.rng)

    assert decision.choice["card_id"] == "black_hybrid_02"
    assert decision.choice["action"] == "remove_one_propaganda_card_from_game"
    assert decision.choice in provider.journalist_action_options(context)


def test_media_mogul_places_own_useful_propaganda_more_often_than_random() -> None:
    rules, cards, provider = _fixture()
    state = create_initial_state(rules, cards, seed=123)
    state.players["P1"].secret_faction_id = "red"
    state.players["P1"].hand = ["propaganda_01", "black_hybrid_02"]
    context = provider.build_context(state, "P1")
    bot = BotFactory().create(BotConfig(id="loyalist", player_id="P1", type="loyalist", randomness=0))

    decision = bot.choose_media_mogul_card(context, provider.media_mogul_card_options(context), state.rng)

    assert decision.choice == "propaganda_01"
    assert decision.choice in provider.media_mogul_card_options(context)


def test_special_role_actions_remain_legal() -> None:
    rules, cards, provider = _fixture()
    state = create_initial_state(rules, cards, seed=321)
    state.players["P1"].secret_faction_id = "red"
    state.players["P1"].hand = ["propaganda_01", "black_hybrid_02"]
    state.propaganda_track.slots = ["propaganda_01", "black_hybrid_02", None]
    context = provider.build_context(state, "P1")
    bot = BotFactory().create(BotConfig(id="deceptive", player_id="P1", type="deceptive"))

    journalist_decision = bot.choose_journalist_action(context, provider.journalist_action_options(context), state.rng)
    media_decision = bot.choose_media_mogul_card(context, provider.media_mogul_card_options(context), state.rng)

    assert journalist_decision.choice in provider.journalist_action_options(context)
    assert media_decision.choice in provider.media_mogul_card_options(context)


def test_special_role_events_contain_reason() -> None:
    rules, cards, _provider = _fixture()
    rules.propaganda.allowed_sources_for_propaganda = "hand"
    bots = [
        BotConfig(id=f"{player.id}_loyalist", player_id=player.id, type="loyalist", randomness=0)
        for player in rules.players
    ]
    engine = GameEngine(rules, cards, seed=123, bots=bots)
    engine.state.round.journalist_player_id = "P1"
    engine.state.round.media_mogul_player_id = "P1"
    engine.state.players["P1"].secret_faction_id = "red"
    engine.state.players["P1"].hand = ["propaganda_01", "black_hybrid_02"]
    engine.state.propaganda_track.slots = ["propaganda_01", "black_hybrid_02", None]

    engine.run_phase("journalist_phase")
    engine.run_phase("media_mogul_phase")

    events = engine.state.export_events_as_dicts()
    journalist_events = [event for event in events if event["event_type"] == "journalist_action_taken"]
    media_events = [event for event in events if event["event_type"] == "media_mogul_action_taken"]

    assert journalist_events[0]["payload"]["reason"]
    assert media_events[0]["payload"]["reason"]
