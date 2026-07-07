from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class FactionConfig(BaseModel):
    id: str
    name: str | None = None
    start_population: int = Field(ge=0)
    eliminated_at_start: bool = False


class PopulationConfig(BaseModel):
    total_population: int = Field(gt=0)
    neutral_start: int = Field(ge=0)


class PlayerConfig(BaseModel):
    id: str
    seat: int = Field(ge=0)
    faction_id: str


class RoleConfig(BaseModel):
    start_player: Literal["random"] | str = "random"
    first_journalist: Literal["left_of_start_player"] | str = "left_of_start_player"
    first_media_mogul: Literal["normal_rules"] | str = "normal_rules"
    saboteur: "SaboteurConfig" = Field(default_factory=lambda: SaboteurConfig())


class SaboteurConfig(BaseModel):
    enabled: bool = False
    count: int = Field(default=0, ge=0)


class PropagandaConfig(BaseModel):
    slots: int = Field(gt=0)
    overflow: Literal["remove_oldest"] | str = "remove_oldest"
    journalist_options: list[
        Literal["discard_one", "place_one_on_top_of_deck", "remove_one_propaganda_card_from_game"]
    ] = Field(default_factory=lambda: ["discard_one", "place_one_on_top_of_deck", "remove_one_propaganda_card_from_game"])
    media_mogul_draw_count: int = Field(default=2, ge=0)
    media_mogul_choice_count: int = Field(default=1, ge=1)
    allowed_sources_for_propaganda: Literal["hand", "deck_draw", "both"] = "hand"
    media_mogul_card_source: Literal["hand"] = "hand"
    media_mogul_card_types: list[Literal["propaganda", "hybrid"]] = Field(default_factory=lambda: ["propaganda", "hybrid"])


class RoundFlowConfig(BaseModel):
    phases: list[str] = Field(min_length=1)
    max_rounds: int = Field(gt=0)


class DraftConfig(BaseModel):
    enabled: bool = True
    starting_hand_size: int = Field(ge=0)
    hidden_research_orders: int = Field(ge=0)
    max_sources: int = Field(ge=0)
    draw_count: int = Field(default=0, ge=0)
    pick_count: int = Field(default=0, ge=0)
    pass_count: int = Field(default=0, ge=0)
    last_player_discard_count: int = Field(default=0, ge=0)
    direction: Literal["clockwise", "counterclockwise"] = "clockwise"


class DeckConfig(BaseModel):
    reshuffle_discard_when_empty: bool = True
    when_not_enough_cards: Literal["draw_less", "error"] = "draw_less"
    log_reshuffle_events: bool = True


class SaboteurWinConditionConfig(BaseModel):
    type: Literal[
        "total_population_zero",
        "all_factions_zero",
        "destroyed_population_at_least",
        "factions_eliminated_at_least",
    ]
    threshold: int | None = None


class VictoryConfig(BaseModel):
    check_timing: list[Literal["after_population_change", "end_of_round", "game_end"]] = Field(
        default_factory=lambda: ["game_end"]
    )
    faction_win_mode: Literal["highest_population_at_game_end"] = "highest_population_at_game_end"
    tie_breakers: Literal["shared_win", "no_winner", "configured_order"] = "shared_win"
    saboteur_win_conditions: list[SaboteurWinConditionConfig] = Field(default_factory=list)


class AnalyticsConfig(BaseModel):
    outputs_dir: str = "outputs/runs"
    event_log_format: Literal["jsonl"] | str = "jsonl"
    save_all_events: bool = False
    sampled_event_logging: bool = True
    minimal_logging: bool = False
    save_last_n_games_events: int = Field(default=1000, ge=0)
    save_interesting_games: bool = True
    faction_winrate_min: float = Field(default=0.18, ge=0, le=1)
    faction_winrate_max: float = Field(default=0.35, ge=0, le=1)
    saboteur_winrate_max: float = Field(default=0.20, ge=0, le=1)
    player_position_advantage_max: float = Field(default=0.35, ge=0, le=1)
    average_rounds_min: float = Field(default=6.0, ge=0)
    average_rounds_max: float = Field(default=12.0, ge=0)
    early_decision_rate_max: float = Field(default=0.25, ge=0, le=1)
    useless_card_rate_max: float = Field(default=0.60, ge=0, le=1)
    overpowered_card_delta_max: float = Field(default=20.0, ge=0)
    propaganda_slot_dominance_max: float = Field(default=0.55, ge=0, le=1)
    research_order_completion_min: float = Field(default=0.10, ge=0, le=1)
    fallback_decision_rate_max: float = Field(default=0.15, ge=0, le=1)


class QualityConfig(BaseModel):
    strict_mode: bool = True
    debug_export_on_error: bool = True


class V03GameSection(BaseModel):
    name: str
    version: str
    player_count: int = Field(ge=2)
    max_rounds: int = Field(gt=0)
    start_player_mode: Literal["random"] | str = "random"


class V03PopulationSection(BaseModel):
    total: int = Field(gt=0)
    start: dict[str, int]
    pools: list[str] = Field(min_length=1)
    eliminated_factions_do_not_return: bool = True

    @model_validator(mode="after")
    def validate_population(self) -> "V03PopulationSection":
        configured_total = sum(self.start.values())
        if configured_total != self.total:
            raise ValueError(
                "population.start values must sum to population.total: "
                f"{configured_total} != {self.total}."
            )
        return self


class V03CardsSection(BaseModel):
    starting_hand_size: int = Field(ge=0)
    automatic_refill_to_hand_size: bool = False
    hand_size_public: bool = True
    hand_content_secret: bool = True


class V03DeckSection(BaseModel):
    shared_main_deck: bool = True
    expected_cards_per_faction: int = Field(ge=0)
    reshuffle_discard_when_empty: bool = True
    when_not_enough_cards: Literal["draw_less", "error"] = "draw_less"
    banished_cards_return: bool = False


class V03JournalistRoleSection(BaseModel):
    enabled: bool = True
    initial_holder: Literal["left_of_start_player"] | str = "left_of_start_player"
    permanent_until_overthrown_by_sources: bool = True


class V03MediaMogulRoleSection(BaseModel):
    enabled: bool = True
    initial_holder: Literal["none"] | str = "none"
    elected_each_round: bool = True


class V03RolesSection(BaseModel):
    journalist: V03JournalistRoleSection
    media_mogul: V03MediaMogulRoleSection


class V03SourcesSection(BaseModel):
    starting_sources: int = Field(ge=0)
    max_sources: int = Field(ge=0)
    used_sources_are_spent: bool = True


class V03ResearchAssignmentsSection(BaseModel):
    starting_assignments: int = Field(ge=0)
    max_assignments: int = Field(ge=0)
    max_completed_per_round: int = Field(ge=0)
    source_reward: int = Field(ge=0)
    allow_discard_and_redraw_if_none_completed: bool = True


class V03PropagandaSection(BaseModel):
    slots: int = Field(gt=0)
    slot_factors: list[int] = Field(min_length=1)
    slot_1_is_newest: bool = True
    slot_4_is_oldest: bool = True
    new_propaganda_goes_to_slot_1: bool = True
    displaced_goes_to_discard: bool = True
    removed_goes_to_discard: bool = True
    banished_goes_to_banished: bool = True
    close_gaps_after_removal: bool = True
    activation_requires_world_history_card_of_same_faction: bool = True
    victory_power_uses_objective_track_power: bool = True

    @model_validator(mode="after")
    def validate_slot_factors(self) -> "V03PropagandaSection":
        if len(self.slot_factors) != self.slots:
            raise ValueError("propaganda.slot_factors length must equal propaganda.slots.")
        return self


class V03DraftSection(BaseModel):
    contribution_required: bool = True
    contribution_cards_per_player: int = Field(ge=0)
    if_no_hand_card_draw_one_forced_contribution: bool = True
    start_player_draws_extra_cards: int = Field(ge=0)
    cards_seen_each_pick: int = Field(ge=0)
    cards_taken_each_pick: int = Field(ge=0)
    cards_passed_each_pick: int = Field(ge=0)
    final_two_cards_go_to_journalist: bool = True


class V03JournalistPhaseSection(BaseModel):
    journalist_draws_extra_card: int = Field(ge=0)
    journalist_pool_size: int = Field(ge=0)
    options: list[Literal["future_set", "remove_propaganda"]] = Field(min_length=1)
    future_set: dict[str, bool] = Field(default_factory=dict)
    remove_propaganda: dict[str, bool] = Field(default_factory=dict)


class V03MediaMogulPhaseSection(BaseModel):
    receives_cards: int = Field(ge=0)
    chooses_one_as_new_propaganda: bool = True
    unchosen_to_discard: bool = True


class V03UrnSection(BaseModel):
    min_cards_per_player_with_hand: int = Field(ge=0)
    max_cards_per_player: int = Field(ge=0)
    anonymous: bool = True
    shuffle_before_reveal: bool = True


class V03WorldHistorySection(BaseModel):
    reveal_all_urn_cards: bool = True
    order_relevant: bool = True
    resolve_left_to_right: bool = True
    discard_world_history_after_victory_check_if_game_continues: bool = True


class V03PowerSection(BaseModel):
    temporary_per_world_history: bool = True
    cannot_go_below_zero: bool = True
    base_power_from_world_history_cards: bool = True
    add_activated_propaganda_power: bool = True


class V03PatternsSection(BaseModel):
    pair: dict[str, Any]
    three_of_a_kind: dict[str, Any]
    straight: dict[str, Any]


class V03TargetingSection(BaseModel):
    determine_from_world_history_transitions: bool = True
    last_target_effect_wins: bool = True
    faction_cannot_attack_itself: bool = True
    no_target_attacks_neutral: bool = True


class V03NeutralImpulseSection(BaseModel):
    enabled: bool = True
    threshold_for_high_recruit: int = Field(ge=0)
    recruit_low: int = Field(ge=0)
    recruit_high: int = Field(ge=0)
    is_not_attack: bool = True
    is_not_successful_attack: bool = True
    is_not_combat_impact: bool = True


class V03CombatImpactBand(BaseModel):
    min: int = Field(ge=0)
    max: int | None = None
    impact: int = Field(ge=0)


class V03CombatSection(BaseModel):
    simultaneous: bool = True
    success_requires_attacker_power_greater_than_defender_power: bool = True
    tie_attack_success_if_effect_allows: bool = True
    successful_tie_attack_base_impact: int = Field(ge=0)
    impact_by_margin: list[V03CombatImpactBand] = Field(min_length=1)
    no_general_impact_cap: bool = True
    default_success_effect: str


class V03VictorySection(BaseModel):
    check_after_world_history_and_combat: bool = True
    check_before_research_assignments: bool = True
    order: list[str] = Field(min_length=1)
    collapse: dict[str, Any]
    hegemony: dict[str, Any]
    dominance: dict[str, Any]
    breakthrough: dict[str, Any]
    simultaneous_same_tier_wins_continue_game: bool = True


class V03TieBreakersSection(BaseModel):
    faction_tiebreaker: dict[str, str]
    player_tiebreaker: dict[str, str]


class RulesV03Config(BaseModel):
    game: V03GameSection
    factions: list[str] = Field(min_length=1)
    population: V03PopulationSection
    cards: V03CardsSection
    deck: V03DeckSection
    zones: list[str] = Field(min_length=1)
    roles: V03RolesSection
    sources: V03SourcesSection
    research_assignments: V03ResearchAssignmentsSection
    round_flow: list[str] = Field(min_length=1)
    propaganda: V03PropagandaSection
    draft: V03DraftSection
    journalist_phase: V03JournalistPhaseSection
    media_mogul_phase: V03MediaMogulPhaseSection
    urn: V03UrnSection
    world_history: V03WorldHistorySection
    power: V03PowerSection
    patterns: V03PatternsSection
    targeting: V03TargetingSection
    neutral_impulse: V03NeutralImpulseSection
    combat: V03CombatSection
    victory: V03VictorySection
    tie_breakers: V03TieBreakersSection

    @model_validator(mode="after")
    def validate_v03_rules(self) -> "RulesV03Config":
        faction_ids = list(self.factions)
        if len(faction_ids) != len(set(faction_ids)):
            raise ValueError("factions must be unique.")
        missing_factions = [faction_id for faction_id in faction_ids if faction_id not in self.population.start]
        if missing_factions:
            raise ValueError(f"population.start missing factions: {', '.join(missing_factions)}.")
        if "neutral" not in self.population.start:
            raise ValueError("population.start must contain neutral.")
        if "destroyed" not in self.population.start:
            raise ValueError("population.start must contain destroyed.")
        if self.urn.max_cards_per_player < self.urn.min_cards_per_player_with_hand:
            raise ValueError("urn.max_cards_per_player must be >= urn.min_cards_per_player_with_hand.")
        return self


class CardConfig(BaseModel):
    id: str
    name: str
    type: Literal["action", "propaganda", "hybrid", "source", "research_order"]
    faction: str | None = None
    strength: int = 0
    count: int = Field(default=1, ge=0)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    action_effects: list[dict[str, Any]] = Field(default_factory=list)
    propaganda_effects: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class BotConfig(BaseModel):
    id: str
    player_id: str | None = None
    type: Literal["random", "heuristic", "loyalist", "deceptive", "saboteur"] = "random"
    name: str | None = None
    skill: float = Field(default=0.7, ge=0, le=1)
    randomness: float = Field(default=0.2, ge=0, le=1)
    aggression: float = Field(default=0.55, ge=0, le=1)
    secrecy: float = Field(default=0.7, ge=0, le=1)
    propaganda_awareness: float = Field(default=0.6, ge=0, le=1)
    risk_tolerance: float = Field(default=0.45, ge=0, le=1)
    weights: dict[str, float] = Field(default_factory=dict)


class GameConfig(BaseModel):
    game_id: str
    player_count: int = Field(ge=2)
    factions: list[FactionConfig]
    population: PopulationConfig
    players: list[PlayerConfig]
    roles: RoleConfig
    propaganda: PropagandaConfig
    round_flow: RoundFlowConfig
    draft: DraftConfig
    deck: DeckConfig = Field(default_factory=DeckConfig)
    victory: VictoryConfig
    analytics: AnalyticsConfig
    quality: QualityConfig = Field(default_factory=QualityConfig)
    cards: list[CardConfig] = Field(default_factory=list)
    bots: list[BotConfig] = Field(default_factory=list)
    v03: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_game_config(self) -> GameConfig:
        faction_ids = [faction.id for faction in self.factions]
        if len(faction_ids) != len(set(faction_ids)):
            raise ValueError("Faction ids must be unique.")

        start_population = sum(faction.start_population for faction in self.factions)
        configured_total = start_population + self.population.neutral_start
        if configured_total != self.population.total_population:
            raise ValueError(
                "Starting faction population plus neutral_start must equal "
                f"total_population: {configured_total} != {self.population.total_population}."
            )

        if len(self.players) != self.player_count:
            raise ValueError(f"Expected {self.player_count} players, got {len(self.players)}.")

        player_ids = [player.id for player in self.players]
        if len(player_ids) != len(set(player_ids)):
            raise ValueError("Player ids must be unique.")

        known_factions = set(faction_ids)
        for player in self.players:
            if player.faction_id not in known_factions:
                raise ValueError(f"Player {player.id} references unknown faction {player.faction_id}.")

        return self
