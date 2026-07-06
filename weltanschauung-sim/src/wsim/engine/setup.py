from __future__ import annotations

from wsim.config import ConfigError
from wsim.core.models import CardConfig, GameConfig
from wsim.core.state import (
    DeckState,
    FactionState,
    GameRng,
    GameState,
    PlayerState,
    PropagandaTrackState,
    RoundState,
)


def create_initial_state(config: GameConfig, cards: list[CardConfig], seed: int) -> GameState:
    rng = GameRng(seed)
    ordered_players = sorted(config.players, key=lambda player: player.seat)
    player_ids = [player.id for player in ordered_players]
    if player_ids != [f"P{index}" for index in range(1, len(player_ids) + 1)]:
        raise ConfigError("Initial setup expects player ids P1, P2, ... in seat order.")

    factions = {
        faction.id: FactionState(
            id=faction.id,
            population=faction.start_population,
            eliminated=faction.eliminated_at_start,
        )
        for faction in config.factions
    }

    enabled_cards = [card for card in cards if card.enabled]
    disabled_cards = sorted(card.id for card in cards if not card.enabled)
    regular_deck = sorted(card.id for card in enabled_cards if card.type != "research_order")
    research_order_pool = sorted(card.id for card in enabled_cards if card.type == "research_order")
    rng.shuffle(regular_deck)
    rng.shuffle(research_order_pool)

    needed_hand_cards = config.draft.starting_hand_size * len(ordered_players)
    if len(regular_deck) < needed_hand_cards:
        raise ConfigError(
            f"Not enough enabled non-research cards for starting hands: {len(regular_deck)} < {needed_hand_cards}."
        )
    needed_research_orders = config.draft.hidden_research_orders * len(ordered_players)
    if len(research_order_pool) < needed_research_orders:
        raise ConfigError(
            "Not enough enabled research_order cards for hidden research orders: "
            f"{len(research_order_pool)} < {needed_research_orders}."
        )

    start_player_id = _choose_start_player(config, player_ids, rng)
    journalist_player_id = _choose_journalist(config, player_ids, start_player_id)
    media_mogul_player_id, media_mogul_notes = _choose_media_mogul(config, player_ids, start_player_id)

    saboteur_player_ids = _choose_saboteurs(config, player_ids, rng)
    secret_faction_by_player = _assign_secret_factions(config, player_ids, saboteur_player_ids, rng)

    players: dict[str, PlayerState] = {}
    for player in ordered_players:
        roles: list[str] = []
        if player.id == journalist_player_id:
            roles.append("journalist")
        if player.id == media_mogul_player_id:
            roles.append("media_mogul")
        if player.id in saboteur_player_ids:
            roles.append("saboteur")

        hand = _draw_many(regular_deck, config.draft.starting_hand_size)
        hidden_orders = _draw_many(research_order_pool, config.draft.hidden_research_orders)
        players[player.id] = PlayerState(
            id=player.id,
            seat=player.seat,
            public_faction_id=player.faction_id,
            secret_faction_id=secret_faction_by_player.get(player.id),
            is_saboteur=player.id in saboteur_player_ids,
            roles=roles,
            hand=hand,
            hidden_research_orders=hidden_orders,
            sources=[],
            max_sources=config.draft.max_sources,
        )

    return GameState(
        seed=seed,
        rng=rng,
        players=players,
        factions=factions,
        neutral_population=config.population.neutral_start,
        deck=DeckState(
            draw_pile=regular_deck,
            research_order_pool=research_order_pool,
            disabled_cards=disabled_cards,
        ),
        propaganda_track=PropagandaTrackState(
            slots=[None for _ in range(config.propaganda.slots)],
            overflow=config.propaganda.overflow,
        ),
        round=RoundState(
            start_player_id=start_player_id,
            journalist_player_id=journalist_player_id,
            media_mogul_player_id=media_mogul_player_id,
            role_assignment_notes=media_mogul_notes,
        ),
    )


def _choose_start_player(config: GameConfig, player_ids: list[str], rng: GameRng) -> str:
    if config.roles.start_player == "random":
        return rng.choice(player_ids)
    if config.roles.start_player in player_ids:
        return config.roles.start_player
    raise ConfigError(f"Unknown configured start_player: {config.roles.start_player}")


def _choose_journalist(config: GameConfig, player_ids: list[str], start_player_id: str) -> str:
    if config.roles.first_journalist == "left_of_start_player":
        start_index = player_ids.index(start_player_id)
        return player_ids[(start_index + 1) % len(player_ids)]
    if config.roles.first_journalist in player_ids:
        return config.roles.first_journalist
    raise ConfigError(f"Unknown configured first_journalist: {config.roles.first_journalist}")


def _choose_media_mogul(config: GameConfig, player_ids: list[str], start_player_id: str) -> tuple[str, list[str]]:
    if config.roles.first_media_mogul == "normal_rules":
        return start_player_id, [
            "TODO: first_media_mogul normal_rules are not implemented yet; assigned to start_player as explicit placeholder."
        ]
    if config.roles.first_media_mogul in player_ids:
        return config.roles.first_media_mogul, []
    raise ConfigError(f"Unknown configured first_media_mogul: {config.roles.first_media_mogul}")


def _choose_saboteurs(config: GameConfig, player_ids: list[str], rng: GameRng) -> set[str]:
    if not config.roles.saboteur.enabled:
        return set()
    if config.roles.saboteur.count != 1:
        raise ConfigError("Only saboteur count 1 is supported by initial setup for now.")
    return set(rng.sample(player_ids, 1))


def _assign_secret_factions(
    config: GameConfig,
    player_ids: list[str],
    saboteur_player_ids: set[str],
    rng: GameRng,
) -> dict[str, str]:
    faction_ids = sorted(faction.id for faction in config.factions)
    rng.shuffle(faction_ids)
    eligible_player_ids = [player_id for player_id in player_ids if player_id not in saboteur_player_ids]
    if len(faction_ids) < len(eligible_player_ids):
        raise ConfigError("Not enough factions to assign secret factions one_each.")
    return dict(zip(eligible_player_ids, faction_ids, strict=False))


def _draw_many(deck: list[str], count: int) -> list[str]:
    drawn = deck[:count]
    del deck[:count]
    return drawn

