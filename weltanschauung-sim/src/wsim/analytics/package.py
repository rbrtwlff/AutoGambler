from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel

from wsim.analytics.inspector import GameInspectError, inspect_game, render_game_markdown
from wsim.analytics.report import generate_report


class AnalysisPackageResult(BaseModel):
    run_dir: Path
    package_dir: Path
    zip_path: Path
    copied_files: list[str]
    skipped_optional_files: list[str]
    interesting_games: list[str]


def export_analysis_package(run_dir: str | Path, *, max_interesting_games: int = 10) -> AnalysisPackageResult:
    run_path = Path(run_dir)
    if not run_path.exists() or not run_path.is_dir():
        raise ValueError(f"Run directory does not exist: {run_path}")

    metrics = generate_report(run_path)
    package_dir = run_path / "analysis_package"
    zip_path = run_path / "analysis_package.zip"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    copied_files: list[str] = []
    skipped_optional: list[str] = []
    for file_name in _required_files():
        copied_files.extend(_copy_if_exists(run_path, package_dir, file_name, optional=False))
    for file_name in _optional_files():
        copied = _copy_if_exists(run_path, package_dir, file_name, optional=True)
        if copied:
            copied_files.extend(copied)
        else:
            skipped_optional.append(file_name)

    interesting_games = _export_interesting_games(run_path, package_dir, max_interesting_games=max_interesting_games)
    (package_dir / "README_FUER_CHATGPT.md").write_text(
        _render_package_readme(run_path, metrics, copied_files, skipped_optional, interesting_games),
        encoding="utf-8",
    )
    copied_files.append("README_FUER_CHATGPT.md")

    _write_zip(package_dir, zip_path)
    return AnalysisPackageResult(
        run_dir=run_path,
        package_dir=package_dir,
        zip_path=zip_path,
        copied_files=sorted(copied_files),
        skipped_optional_files=sorted(skipped_optional),
        interesting_games=interesting_games,
    )


def _required_files() -> list[str]:
    return [
        "report.md",
        "metrics.json",
        "metrics_cards.json",
        "metrics_propaganda.json",
        "metrics_bots.json",
        "metrics_v0_3.json",
    ]


def _optional_files() -> list[str]:
    return ["comparison_report.md"]


def _copy_if_exists(source_dir: Path, target_dir: Path, file_name: str, *, optional: bool) -> list[str]:
    source = source_dir / file_name
    if not source.exists():
        if optional:
            return []
        return []
    target = target_dir / file_name
    shutil.copy2(source, target)
    return [file_name]


def _export_interesting_games(run_dir: Path, package_dir: Path, *, max_interesting_games: int) -> list[str]:
    game_ids = _interesting_game_ids(run_dir, max_interesting_games=max_interesting_games)
    games_dir = package_dir / "interesting_games"
    games_dir.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    for game_id in game_ids:
        try:
            report = inspect_game(run_dir, game_id, analysis_mode=True)
        except GameInspectError:
            continue
        file_name = f"game_{int(report['game_index']):06d}.md"
        (games_dir / file_name).write_text(render_game_markdown(report), encoding="utf-8")
        exported.append(f"interesting_games/{file_name}")
    return exported


def _interesting_game_ids(run_dir: Path, *, max_interesting_games: int) -> list[int]:
    frame = _read_table(run_dir, "game_summaries")
    if frame.is_empty():
        return []
    rows = frame.to_dicts()
    median_rounds = frame["rounds_played"].median() if "rounds_played" in frame.columns else None

    def score(row: dict[str, Any]) -> tuple[int, int]:
        points = 0
        if row.get("winner_type") == "saboteur":
            points += 5
        if row.get("tie_info") not in (None, "", "null"):
            points += 4
        if median_rounds is not None and row.get("rounds_played") is not None and int(row["rounds_played"]) <= median_rounds:
            points += 2
        if row.get("winning_condition"):
            points += 1
        return points, -int(row.get("game_index") or 0)

    ranked = sorted(rows, key=score, reverse=True)
    selected = [int(row["game_index"]) for row in ranked[:max(0, max_interesting_games)] if row.get("game_index") is not None]
    if selected:
        return selected
    return [int(row["game_index"]) for row in rows[:max(0, max_interesting_games)] if row.get("game_index") is not None]


def _render_package_readme(
    run_dir: Path,
    metrics: dict[str, Any],
    copied_files: list[str],
    skipped_optional: list[str],
    interesting_games: list[str],
) -> str:
    overview = metrics.get("overview", {})
    rounds = overview.get("rounds", {})
    warnings = metrics.get("warnings", [])
    lines = [
        f"# README fuer ChatGPT: {run_dir.name}",
        "",
        "Bitte lies zuerst `report.md`. Danach nutze die JSON-Dateien fuer Details und die Einzelspiele unter `interesting_games/` fuer konkrete Spielverlaeufe.",
        "",
        "## Enthaltene Dateien",
        *[f"- `{file_name}`" for file_name in sorted(copied_files)],
        *[f"- `{file_name}`" for file_name in interesting_games],
        "",
        "## Optionale Dateien",
        *([f"- Nicht vorhanden und deshalb uebersprungen: `{file_name}`" for file_name in sorted(skipped_optional)] or ["- Keine optionalen Dateien uebersprungen."]),
        "",
        "## Kurze Zusammenfassung des Runs",
        f"- Run: `{run_dir.name}`",
        f"- Spiele: {overview.get('game_count', 'n/a')}",
        f"- Durchschnittliche Rundenzahl: {_fmt(rounds.get('average'))}",
        f"- Saboteur-Siegquote: {_fmt_pct(overview.get('saboteur_win_rate'))}",
        f"- Automatische Balancing-Warnungen: {len(warnings)}",
        "",
        "## Fragen, die ChatGPT beantworten soll",
        "- Welche Fraktionen, Spielerpositionen oder Rollen wirken aktuell ueber- oder unterbalanciert?",
        "- Welche Karten oder Propaganda-Konstellationen sind auffaellig stark, schwach oder wirkungslos?",
        "- Welche Bot-Typen verhalten sich plausibel, und wo entstehen Fallback- oder Random-Entscheidungen?",
        "- Welche konkreten Regel- oder Kartenanpassungen waeren als naechste Experimente sinnvoll?",
        "- Welche Einzelspiele aus `interesting_games/` illustrieren die wichtigsten Balancing-Probleme?",
        "",
    ]
    return "\n".join(lines)


def _write_zip(package_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path("analysis_package") / path.relative_to(package_dir))


def _read_table(run_dir: Path, table_name: str) -> pl.DataFrame:
    csv_path = run_dir / f"{table_name}.csv"
    parquet_path = run_dir / f"{table_name}.parquet"
    if csv_path.exists():
        return pl.read_csv(csv_path)
    if parquet_path.exists():
        return pl.read_parquet(parquet_path)
    return pl.DataFrame()


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
