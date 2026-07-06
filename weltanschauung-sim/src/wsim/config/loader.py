from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from wsim.core.models import GameConfig


class ConfigError(ValueError):
    """Raised when a YAML config cannot be loaded or validated."""


def load_yaml(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")
    if not config_path.is_file():
        raise ConfigError(f"Config path is not a file: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc

    if data is None:
        raise ConfigError(f"Config file is empty: {config_path}")
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping/object: {config_path}")
    return data


def load_rules_config(path: str | Path) -> GameConfig:
    data = load_yaml(path)
    try:
        return GameConfig.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise ConfigError(f"Invalid rules config {Path(path)}: {details}") from exc

