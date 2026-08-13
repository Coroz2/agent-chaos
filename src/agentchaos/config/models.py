"""Validated scenario models."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    """Base model that rejects accidental schema extensions."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandConfig(StrictModel):
    command: list[str] = Field(min_length=1)
    cwd: Path | None = None
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("command")
    @classmethod
    def validate_command(cls, command: list[str]) -> list[str]:
        if any(not argument for argument in command):
            raise ValueError("command arguments must not be empty")
        return command


class ReadinessConfig(StrictModel):
    path: str
    timeout_seconds: float = Field(default=5.0, gt=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("readiness path must start with '/'")
        return path


class DependencyStartConfig(CommandConfig):
    readiness: ReadinessConfig


class HttpDependencyConfig(StrictModel):
    type: Literal["http"]
    base_url: AnyHttpUrl
    start: DependencyStartConfig | None = None


class WorkloadConfig(CommandConfig):
    name: str | None = Field(default=None, min_length=1)
    proxy_url_env: str | None = None

    @field_validator("proxy_url_env")
    @classmethod
    def validate_proxy_url_env(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("proxy_url_env must be a valid environment variable name")
        return value


class FaultTarget(StrictModel):
    method: str | None = None
    path: str

    @field_validator("method")
    @classmethod
    def normalize_method(cls, method: str | None) -> str | None:
        return method.upper() if method is not None else None

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("fault target path must start with '/'")
        if any(character in path for character in "?[]"):
            raise ValueError("fault target path supports only '*' as a wildcard")
        return path


class OccurrenceTriggerConfig(StrictModel):
    occurrence: int = Field(gt=0)


class HttpLatencyFault(StrictModel):
    type: Literal["http_latency"]
    target: FaultTarget
    trigger: OccurrenceTriggerConfig
    latency_ms: int = Field(gt=0)


class HttpErrorFault(StrictModel):
    type: Literal["http_error"]
    target: FaultTarget
    trigger: OccurrenceTriggerConfig
    status_code: int = Field(ge=400, le=599)


FaultConfig = Annotated[HttpLatencyFault | HttpErrorFault, Field(discriminator="type")]


class SuccessConfig(StrictModel):
    exit_code: int = Field(ge=0, le=255)


class Scenario(StrictModel):
    schema_version: Literal[1]
    name: str = Field(min_length=1)
    dependency: HttpDependencyConfig
    workload: WorkloadConfig
    fault: FaultConfig | None = None
    success: SuccessConfig
    timeout_seconds: float = Field(default=60.0, gt=0)

    def resolve_path(self, scenario_path: Path, configured_path: Path | None) -> Path:
        """Resolve a configured working directory relative to the scenario file."""
        base = scenario_path.parent.resolve()
        return base if configured_path is None else (base / configured_path).resolve()
