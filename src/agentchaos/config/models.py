"""Validated scenario models."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_serializer,
    field_validator,
    model_validator,
)


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
    occurrence: StrictInt | None = Field(default=None, gt=0)
    occurrences: tuple[Annotated[StrictInt, Field(gt=0)], ...] | None = Field(
        default=None,
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_trigger_form(self) -> OccurrenceTriggerConfig:
        if (self.occurrence is None) == (self.occurrences is None):
            raise ValueError("specify exactly one of occurrence or occurrences")
        if self.occurrences is not None and any(
            current >= following
            for current, following in zip(self.occurrences, self.occurrences[1:], strict=False)
        ):
            raise ValueError("occurrences must be in strictly increasing order")
        return self

    @property
    def schedule(self) -> tuple[int, ...]:
        """Return the normalized occurrence schedule."""
        if self.occurrences is not None:
            return self.occurrences
        assert self.occurrence is not None
        return (self.occurrence,)


class ProbabilityWindowConfig(StrictModel):
    start_occurrence: StrictInt = Field(gt=0)
    end_occurrence: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> ProbabilityWindowConfig:
        if self.start_occurrence > self.end_occurrence:
            raise ValueError("window start_occurrence must not exceed end_occurrence")
        return self

    @property
    def size(self) -> int:
        return self.end_occurrence - self.start_occurrence + 1


class ProbabilityTriggerConfig(StrictModel):
    probability: Decimal
    seed: StrictInt = Field(ge=0, le=(2**64) - 1)
    window: ProbabilityWindowConfig

    @field_validator("probability", mode="before")
    @classmethod
    def validate_probability_input(cls, value: Any) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("probability must be a numeric value")
        probability = Decimal(str(value))
        if not probability.is_finite() or probability <= 0 or probability > 1:
            raise ValueError("probability must be greater than 0 and at most 1")
        exponent = probability.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -6:
            raise ValueError("probability supports at most six decimal places")
        return probability

    @field_serializer("probability")
    def serialize_probability(self, probability: Decimal) -> float:
        return float(probability)


TriggerConfig = OccurrenceTriggerConfig | ProbabilityTriggerConfig


class HttpLatencyFault(StrictModel):
    type: Literal["http_latency"]
    target: FaultTarget
    trigger: TriggerConfig
    latency_ms: int = Field(gt=0)


class HttpErrorFault(StrictModel):
    type: Literal["http_error"]
    target: FaultTarget
    trigger: TriggerConfig
    status_code: int = Field(ge=400, le=599)


class HttpRateLimitFault(StrictModel):
    type: Literal["http_rate_limit"]
    target: FaultTarget
    trigger: TriggerConfig
    retry_after_seconds: int = Field(ge=0)


class HttpMalformedJsonFault(StrictModel):
    type: Literal["http_malformed_json"]
    target: FaultTarget
    trigger: TriggerConfig


class HttpDisconnectFault(StrictModel):
    type: Literal["http_disconnect"]
    target: FaultTarget
    trigger: TriggerConfig


FaultConfig = Annotated[
    HttpLatencyFault
    | HttpErrorFault
    | HttpRateLimitFault
    | HttpMalformedJsonFault
    | HttpDisconnectFault,
    Field(discriminator="type"),
]


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
