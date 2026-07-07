from __future__ import annotations

from wsim.config import ConfigError
from wsim.core.events import EventBus, EventType
from wsim.core.models import CardConfig, GameConfig
from wsim.core.state import (
    CardInstance,
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
    event_bus = EventBus(run_id=f"{config.game_id}-{seed}", game_id=config.game_id)
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
    event_bus.emit(
        EventType.GAME_STARTED,
        {
            "seed": seed,
            "player_ids": player_ids,
            "faction_ids": list(factions),
            "total_population": config.population.total_population,
        },
    )

    deck = build_deck(cards, rng)

    needed_hand_cards = config.draft.starting_hand_size * len(ordered_players)
    if len(deck.draw_pile) < needed_hand_cards:
        raise ConfigError(
            f"Not enough enabled non-research card instances for starting hands: {len(deck.draw_pile)} < {needed_hand_cards}."
        )
    needed_research_orders = config.draft.hidden_research_orders * len(ordered_players)
    if len(deck.research_order_pool) < needed_research_orders:
        raise ConfigError(
            "Not enough enabled research_order card instances for hidden research orders: "
            f"{len(deck.research_order_pool)} < {needed_research_orders}."
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

        hand = deck.draw_cards(
            config.draft.starting_hand_size,
            config=config.deck,
            rng=rng,
            event_bus=event_bus,
            reason="starting_hand",
            player_id=player.id,
            destination="hand",
            game_id=config.game_id,
            round_number=0,
            phase="setup",
        )
        hidden_orders = _draw_many(deck.research_order_pool, config.draft.hidden_research_orders)
        for instance_id in hidden_orders:
            event_bus.emit(
                EventType.CARD_DRAWN,
                {
                    "player_id": player.id,
                    "card_id": deck.logical_card_id(instance_id),
                    "instance_id": instance_id,
                    "destination": "hidden_research_orders",
                    "reason": "initial_research_order",
                },
            )
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

    event_bus.emit(
        EventType.INITIAL_STATE_CREATED,
        {
            "start_player_id": start_player_id,
            "journalist_player_id": journalist_player_id,
            "media_mogul_player_id": media_mogul_player_id,
            "saboteur_player_ids": sorted(saboteur_player_ids),
            "secret_faction_by_player": secret_faction_by_player,
            "hand_size": config.draft.starting_hand_size,
            "hidden_research_orders": config.draft.hidden_research_orders,
            "remaining_draw_pile": len(deck.draw_pile),
            "remaining_research_order_pool": len(deck.research_order_pool),
            "neutral_population": config.population.neutral_start,
            "faction_population": {faction_id: faction.population for faction_id, faction in factions.items()},
        },
    )

    return GameState(
        seed=seed,
        rng=rng,
        players=players,
        factions=factions,
        neutral_population=config.population.neutral_start,
        deck=deck,
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
        event_log=event_bus.event_log,
    )


def build_deck(cards: list[CardConfig], rng: GameRng) -> DeckState:
    instances: dict[str, CardInstance] = {}
    regular_deck: list[str] = []
    research_order_pool: list[str] = []
    disabled_cards = sorted(card.id for card in cards if not card.enabled)
    for card in sorted((card for card in cards if card.enabled), key=lambda item: item.id):
        for copy_index in range(card.count):
            instance_id = card.id if card.count == 1 else f"{card.id}__{copy_index + 1:03d}"
            instances[instance_id] = CardInstance(
                instance_id=instance_id,
                card_id=card.id,
                name=card.name,
                type=card.type,
                faction=card.faction,
                strength=card.strength,
                tags=list(card.tags),
            )
            if card.type == "research_order":
                research_order_pool.append(instance_id)
            else:
                regular_deck.append(instance_id)
    rng.shuffle(regular_deck)
    rng.shuffle(research_order_pool)
    return DeckState(
        draw_pile=regular_deck,
        research_order_pool=research_order_pool,
        disabled_cards=disabled_cards,
        card_instances=instances,
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
    if config.roles.first_media_mogul == "none":
        return start_player_id, [
            "Initial media mogul holder is temporary; v0.3 media_mogul_election assigns the active holder each round."
        ]
    if config.roles.first_media_mogul == "normal_rules":
        return start_player_id, [
            "Initial media mogul holder resolved to start_player until the first configured media mogul phase."
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
