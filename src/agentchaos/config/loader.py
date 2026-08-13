"""YAML scenario loading."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentchaos.config.models import Scenario


class ScenarioLoadError(ValueError):
    """Raised when a scenario cannot be parsed or validated."""


def load_scenario(path: Path) -> Scenario:
    """Load and validate a scenario YAML file."""
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ScenarioLoadError(str(error)) from error

    if not isinstance(raw, dict):
        raise ScenarioLoadError("scenario must be a YAML mapping")

    try:
        return Scenario.model_validate(raw)
    except ValidationError as error:
        raise ScenarioLoadError(str(error)) from error
