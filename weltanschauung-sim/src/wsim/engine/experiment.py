from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from rich.progress import track

from wsim.analytics import generate_report
from wsim.config import ConfigError, load_cards_config, load_yaml
from wsim.core.models import BotConfig, GameConfig
from wsim.engine.batch import BatchRunResult, SimulationBatchRunner


class ExperimentVariant(BaseModel):
    name: str
    path: Path
    description: str | None = None
    rules: GameConfig
    bots: list[BotConfig]


class ExperimentResult(BaseModel):
    experiment_id: str
    output_dir: Path
    variants: list[str]
    games_per_variant: int
    master_seed: int
    comparison_metrics_path: Path
    comparison_report_path: Path


class ExperimentRunner:
    def __init__(
        self,
        *,
        base_rules_path: str | Path,
        variants_path: str | Path,
        cards_path: str | Path,
        bots_path: str | Path,
        games_per_variant: int,
        master_seed: int,
        output_dir: Path,
        show_progress: bool = True,
    ) -> None:
        self.base_rules_path = Path(base_rules_path)
        self.variants_path = Path(variants_path)
        self.cards_path = Path(cards_path)
        self.bots_path = Path(bots_path)
        self.games_per_variant = games_per_variant
        self.master_seed = master_seed
        self.output_dir = output_dir
        self.show_progress = show_progress
        self.experiment_id = output_dir.name

    def load_variants(self) -> list[ExperimentVariant]:
        variant_files = _variant_files(self.variants_path)
        if not variant_files:
            raise ConfigError(f"No experiment variants found in {self.variants_path}.")

        base_rules_data = load_yaml(self.base_rules_path)
        base_bots_data = load_yaml(self.bots_path)
        variants: list[ExperimentVariant] = []
        for variant_file in variant_files:
            variants.append(_load_variant(variant_file, base_rules_data, base_bots_data))
        return variants

    def run(self) -> ExperimentResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        variants = self.load_variants()
        iterable = variants
        if self.show_progress:
            iterable = track(variants, total=len(variants), description="Running variants")

        variant_metrics: list[dict[str, Any]] = []
        batch_results: list[BatchRunResult] = []
        for variant_index, variant in enumerate(iterable):
            variant_output = self.output_dir / variant.name
            cards = load_cards_config(self.cards_path, rules_config=variant.rules)
            batch_result = SimulationBatchRunner(
                rules=variant.rules,
                cards=cards,
                bots=variant.bots,
                games=self.games_per_variant,
                master_seed=self.master_seed + variant_index * max(1, self.games_per_variant),
                output_dir=variant_output,
                show_progress=False,
                source_paths={
                    "base_rules": str(self.base_rules_path),
                    "variant": str(variant.path),
                    "cards": str(self.cards_path),
                    "bots": str(self.bots_path),
                },
            ).run()
            metrics = generate_report(variant_output)
            metrics["variant_name"] = variant.name
            metrics["variant_description"] = variant.description
            variant_metrics.append(metrics)
            batch_results.append(batch_result)

        comparison_metrics = build_comparison_metrics(
            experiment_id=self.experiment_id,
            variants=variants,
            variant_metrics=variant_metrics,
            batch_results=batch_results,
            games_per_variant=self.games_per_variant,
            master_seed=self.master_seed,
        )
        metrics_path = self.output_dir / "comparison_metrics.json"
        report_path = self.output_dir / "comparison_report.md"
        metrics_path.write_text(
            json.dumps(comparison_metrics, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report_path.write_text(render_comparison_report(comparison_metrics), encoding="utf-8")
        return ExperimentResult(
            experiment_id=self.experiment_id,
            output_dir=self.output_dir,
            variants=[variant.name for variant in variants],
            games_per_variant=self.games_per_variant,
            master_seed=self.master_seed,
            comparison_metrics_path=metrics_path,
            comparison_report_path=report_path,
        )


def build_comparison_metrics(
    *,
    experiment_id: str,
    variants: list[ExperimentVariant],
    variant_metrics: list[dict[str, Any]],
    batch_results: list[BatchRunResult],
    games_per_variant: int,
    master_seed: int,
) -> dict[str, Any]:
    rows = []
    for variant, metrics, batch_result in zip(variants, variant_metrics, batch_results):
        overview = metrics.get("overview", {})
        population = metrics.get("population", {})
        cards = metrics.get("cards", {})
        propaganda = metrics.get("propaganda_advanced", {})
        rows.append(
            {
                "variant": variant.name,
                "description": variant.description,
                "run_dir": str(batch_result.output_dir),
                "games": overview.get("game_count", batch_result.games),
                "faction_win_rates": overview.get("faction_win_rates", {}),
                "saboteur_win_rate": overview.get("saboteur_win_rate", 0.0),
                "average_rounds": overview.get("rounds", {}).get("average"),
                "average_population_by_round": population.get("average_population_by_round", []),
                "card_outliers": _card_outliers(cards.get("cards", {})),
                "propaganda_outliers": _propaganda_outliers(propaganda),
                "warnings": metrics.get("warnings", []),
            }
        )

    return {
        "experiment_id": experiment_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "games_per_variant": games_per_variant,
        "master_seed": master_seed,
        "variant_count": len(rows),
        "variants": rows,
    }


def render_comparison_report(metrics: dict[str, Any]) -> str:
    lines = [
        f"# Experiment-Vergleich: {metrics['experiment_id']}",
        "",
        "Dieser Report vergleicht mehrere Regelvarianten auf Basis der normalen Simulationsreports.",
        "",
        f"- Varianten: {metrics['variant_count']}",
        f"- Spiele je Variante: {metrics['games_per_variant']}",
        f"- Master-Seed: {metrics['master_seed']}",
        "",
        "## Kurzvergleich",
        _comparison_table(metrics["variants"]),
        "",
        "## Balancing-Warnungen",
        _warnings_section(metrics["variants"]),
        "",
        "## Karten-Ausreisser",
        _outlier_section(metrics["variants"], "card_outliers"),
        "",
        "## Propaganda-Ausreisser",
        _outlier_section(metrics["variants"], "propaganda_outliers"),
        "",
        "## Bevoelkerungsverlauf",
        _population_section(metrics["variants"]),
        "",
    ]
    return "\n".join(lines)


def _load_variant(
    variant_file: Path,
    base_rules_data: dict[str, Any],
    base_bots_data: dict[str, Any],
) -> ExperimentVariant:
    variant_data = load_yaml(variant_file)
    rules_override = _rules_override(variant_data)
    rules_data = _deep_merge(base_rules_data, rules_override)
    rules_data["game_id"] = str(variant_data.get("game_id") or f"{base_rules_data.get('game_id', 'game')}_{variant_file.stem}")

    try:
        rules = GameConfig.model_validate(rules_data)
        bots = _variant_bots(variant_data, base_bots_data, rules)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise ConfigError(f"Invalid experiment variant {variant_file}: {details}") from exc

    return ExperimentVariant(
        name=_safe_variant_name(str(variant_data.get("name") or variant_file.stem)),
        path=variant_file,
        description=variant_data.get("description"),
        rules=rules,
        bots=bots,
    )


def _variant_bots(variant_data: dict[str, Any], base_bots_data: dict[str, Any], rules: GameConfig) -> list[BotConfig]:
    bots_override = variant_data.get("bots")
    if bots_override is None:
        bots_data = deepcopy(base_bots_data)
    elif isinstance(bots_override, list):
        bots_data = {"bots": bots_override}
    elif isinstance(bots_override, dict):
        bots_data = _merge_bot_overrides(base_bots_data, bots_override)
    else:
        raise ConfigError("Experiment variant 'bots' must be a list or mapping.")

    raw_bots = bots_data.get("bots")
    if not isinstance(raw_bots, list):
        raise ConfigError("Experiment bot config must contain a 'bots' list.")
    bots = [BotConfig.model_validate(item) for item in raw_bots]
    _validate_variant_bots(bots, rules)
    return bots


def _validate_variant_bots(bots: list[BotConfig], rules: GameConfig) -> None:
    bot_ids = [bot.id for bot in bots]
    duplicates = sorted({bot_id for bot_id in bot_ids if bot_ids.count(bot_id) > 1})
    if duplicates:
        raise ConfigError(f"Bot ids must be unique. Duplicates: {', '.join(duplicates)}")
    player_ids = {player.id for player in rules.players}
    for bot in bots:
        if bot.player_id is not None and bot.player_id not in player_ids:
            raise ConfigError(f"Bot {bot.id} references unknown player {bot.player_id}.")


def _merge_bot_overrides(base_bots_data: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    bots = [deepcopy(item) for item in base_bots_data.get("bots", [])]
    defaults = override.get("defaults", {})
    if defaults:
        bots = [_deep_merge(bot, defaults) for bot in bots]

    by_id = override.get("by_id", {})
    if by_id:
        for bot in bots:
            patch = by_id.get(bot.get("id")) or by_id.get(bot.get("player_id"))
            if patch:
                bot.update(_deep_merge(bot, patch))

    if "replace" in override:
        bots = override["replace"]
    return {"bots": bots}


def _rules_override(variant_data: dict[str, Any]) -> dict[str, Any]:
    if "rules" in variant_data:
        rules = variant_data["rules"]
        if not isinstance(rules, dict):
            raise ConfigError("Experiment variant 'rules' must be a mapping.")
        return rules
    metadata_keys = {"name", "description", "bots"}
    return {key: value for key, value in variant_data.items() if key not in metadata_keys}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _variant_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise ConfigError(f"Experiment variants path does not exist: {path}")
    if not path.is_dir():
        raise ConfigError(f"Experiment variants path is not a file or directory: {path}")
    return sorted(file for file in path.glob("*.yaml") if file.is_file())


def _safe_variant_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in name.strip())
    return cleaned or "variant"


def _card_outliers(cards: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    outliers = []
    for card_id, values in cards.items():
        swing = float(values.get("swing_value") or 0)
        ineffective = float(values.get("ineffectiveness_rate") or 0)
        if swing > 0 or ineffective > 0:
            outliers.append({"card_id": card_id, "swing_value": swing, "ineffectiveness_rate": ineffective})
    return sorted(outliers, key=lambda item: (item["swing_value"], item["ineffectiveness_rate"]), reverse=True)[:10]


def _propaganda_outliers(propaganda: dict[str, Any]) -> list[dict[str, Any]]:
    outliers = []
    if propaganda.get("dominant_slot") is not None:
        outliers.append(
            {
                "kind": "dominant_slot",
                "slot": propaganda.get("dominant_slot"),
                "share": propaganda.get("dominant_slot_share", 0.0),
            }
        )
    for constellation in propaganda.get("least_effective_constellations", [])[:5]:
        outliers.append({"kind": "least_effective_constellation", **constellation})
    return outliers


def _comparison_table(variants: list[dict[str, Any]]) -> str:
    if not variants:
        return "- Keine Varianten."
    lines = ["| Variante | Spiele | Saboteur | Runden | Fraktions-Siegquoten |", "| --- | ---: | ---: | ---: | --- |"]
    for variant in variants:
        faction_rates = ", ".join(
            f"{name}: {_fmt_pct(value)}" for name, value in variant.get("faction_win_rates", {}).items()
        )
        lines.append(
            f"| {variant['variant']} | {variant.get('games', 0)} | {_fmt_pct(variant.get('saboteur_win_rate'))} | "
            f"{_fmt(variant.get('average_rounds'))} | {faction_rates or 'n/a'} |"
        )
    return "\n".join(lines)


def _warnings_section(variants: list[dict[str, Any]]) -> str:
    lines = []
    for variant in variants:
        warnings = variant.get("warnings", [])
        if not warnings:
            lines.append(f"- {variant['variant']}: keine Warnungen")
            continue
        for warning in warnings:
            lines.append(f"- {variant['variant']}: {warning.get('message', 'WARNING')}")
    return "\n".join(lines) if lines else "- Keine Warnungen."


def _outlier_section(variants: list[dict[str, Any]], key: str) -> str:
    lines = []
    for variant in variants:
        outliers = variant.get(key, [])
        if not outliers:
            lines.append(f"- {variant['variant']}: keine Ausreisser")
        else:
            lines.append(f"- {variant['variant']}: {json.dumps(outliers[:5], ensure_ascii=True, sort_keys=True)}")
    return "\n".join(lines)


def _population_section(variants: list[dict[str, Any]]) -> str:
    lines = []
    for variant in variants:
        rows = variant.get("average_population_by_round", [])
        preview = rows[:3]
        lines.append(f"- {variant['variant']}: {json.dumps(preview, ensure_ascii=True, sort_keys=True)}")
    return "\n".join(lines) if lines else "- Keine Bevoelkerungsdaten."


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"
