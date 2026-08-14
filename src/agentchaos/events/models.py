"""Versioned event envelope and typed payloads."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    DEPENDENCY_STARTED = "DEPENDENCY_STARTED"
    DEPENDENCY_READY = "DEPENDENCY_READY"
    INJECTOR_STARTED = "INJECTOR_STARTED"
    WORKLOAD_STARTED = "WORKLOAD_STARTED"
    OPERATION_OBSERVED = "OPERATION_OBSERVED"
    FAULT_INJECTED = "FAULT_INJECTED"
    OPERATION_FAILED = "OPERATION_FAILED"
    RETRY_OBSERVED = "RETRY_OBSERVED"
    OPERATION_SUCCEEDED = "OPERATION_SUCCEEDED"
    WORKLOAD_COMPLETED = "WORKLOAD_COMPLETED"
    DEPENDENCY_STOPPED = "DEPENDENCY_STOPPED"
    RUN_ERROR = "RUN_ERROR"
    RUN_COMPLETED = "RUN_COMPLETED"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStartedPayload(Payload):
    kind: Literal[EventType.RUN_STARTED] = EventType.RUN_STARTED
    scenario_name: str


class DependencyStartedPayload(Payload):
    kind: Literal[EventType.DEPENDENCY_STARTED] = EventType.DEPENDENCY_STARTED
    base_url: str
    pid: int


class DependencyReadyPayload(Payload):
    kind: Literal[EventType.DEPENDENCY_READY] = EventType.DEPENDENCY_READY
    base_url: str
    readiness_path: str


class InjectorStartedPayload(Payload):
    kind: Literal[EventType.INJECTOR_STARTED] = EventType.INJECTOR_STARTED
    proxy_url: str
    upstream_url: str


class WorkloadStartedPayload(Payload):
    kind: Literal[EventType.WORKLOAD_STARTED] = EventType.WORKLOAD_STARTED
    name: str
    pid: int


class OperationObservedPayload(Payload):
    kind: Literal[EventType.OPERATION_OBSERVED] = EventType.OPERATION_OBSERVED
    operation_id: str
    protocol: Literal["http"] = "http"
    method: str
    path: str
    query_hash: str
    body_hash: str
    fingerprint: str


class FaultInjectedPayload(Payload):
    kind: Literal[EventType.FAULT_INJECTED] = EventType.FAULT_INJECTED
    operation_id: str
    fault_type: Literal["http_latency", "http_error", "http_rate_limit", "http_malformed_json"]
    parameters: dict[str, int]


class OperationFailedPayload(Payload):
    kind: Literal[EventType.OPERATION_FAILED] = EventType.OPERATION_FAILED
    operation_id: str
    fingerprint: str
    failure_kind: str
    status_code: int | None = None
    fault_related: bool = False


class RetryObservedPayload(Payload):
    kind: Literal[EventType.RETRY_OBSERVED] = EventType.RETRY_OBSERVED
    operation_id: str
    retry_of_operation_id: str
    fingerprint: str
    attempt: int


class OperationSucceededPayload(Payload):
    kind: Literal[EventType.OPERATION_SUCCEEDED] = EventType.OPERATION_SUCCEEDED
    operation_id: str
    fingerprint: str
    status_code: int
    duration_ms: int
    fault_related: bool = False


class WorkloadCompletedPayload(Payload):
    kind: Literal[EventType.WORKLOAD_COMPLETED] = EventType.WORKLOAD_COMPLETED
    exit_code: int | None
    timed_out: bool
    interrupted: bool
    duration_ms: int
    error: str | None = None


class DependencyStoppedPayload(Payload):
    kind: Literal[EventType.DEPENDENCY_STOPPED] = EventType.DEPENDENCY_STOPPED
    exit_code: int | None


class RunErrorPayload(Payload):
    kind: Literal[EventType.RUN_ERROR] = EventType.RUN_ERROR
    reason_code: str
    message: str


class RunCompletedPayload(Payload):
    kind: Literal[EventType.RUN_COMPLETED] = EventType.RUN_COMPLETED
    result: Literal["PASSED", "RECOVERED", "FAILED"]
    reason_code: str
    duration_ms: int


EventPayload = Annotated[
    RunStartedPayload
    | DependencyStartedPayload
    | DependencyReadyPayload
    | InjectorStartedPayload
    | WorkloadStartedPayload
    | OperationObservedPayload
    | FaultInjectedPayload
    | OperationFailedPayload
    | RetryObservedPayload
    | OperationSucceededPayload
    | WorkloadCompletedPayload
    | DependencyStoppedPayload
    | RunErrorPayload
    | RunCompletedPayload,
    Field(discriminator="kind"),
]


class Event(BaseModel):
    """Stable event envelope stored as one JSON object per line."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = 1
    event_id: str
    run_id: str
    sequence: int = Field(gt=0)
    timestamp: str
    elapsed_ms: int = Field(ge=0)
    event_type: EventType = Field(alias="type")
    component: str
    payload: EventPayload

    @model_validator(mode="after")
    def event_type_matches_payload(self) -> Event:
        if self.event_type != self.payload.kind:
            raise ValueError("event type does not match payload kind")
        return self
