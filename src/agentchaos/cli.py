"""Agent Chaos command-line interface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from agentchaos import __version__
from agentchaos.config.loader import ScenarioLoadError, load_scenario
from agentchaos.events.models import (
    Event,
    EventType,
    FaultInjectedPayload,
    OperationFailedPayload,
    OperationObservedPayload,
    OperationSucceededPayload,
    RetryObservedPayload,
)
from agentchaos.runtime.orchestrator import RunExecution, run_experiment

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agentchaos {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """Chaos engineering for autonomous AI agents."""


@app.command()
def version() -> None:
    """Show the installed Agent Chaos version."""
    typer.echo(f"agentchaos {__version__}")


@app.command()
def validate(scenario: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Validate a scenario without running it."""
    try:
        loaded = load_scenario(scenario)
    except ScenarioLoadError as error:
        typer.echo(f"Invalid scenario:\n{error}", err=True)
        raise typer.Exit(2) from error
    typer.echo(f"Valid scenario: {loaded.name}")


@app.command()
def run(
    scenario: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory in which run folders are created."),
    ] = Path(".agentchaos/runs"),
) -> None:
    """Run one chaos experiment."""
    try:
        loaded = load_scenario(scenario)
    except ScenarioLoadError as error:
        typer.echo(f"Invalid scenario:\n{error}", err=True)
        raise typer.Exit(2) from error

    typer.echo("Agent Chaos\n")
    typer.echo(f"Experiment: {loaded.name}")
    typer.echo(f"Workload: {loaded.workload.name or loaded.workload.command[0]}")
    typer.echo("\nStarting experiment...\n")
    try:
        execution = asyncio.run(
            run_experiment(
                loaded,
                scenario.resolve(),
                output_dir,
                event_listener=_render_event,
            )
        )
    except KeyboardInterrupt as error:
        raise typer.Exit(130) from error
    except Exception as error:
        typer.echo(f"Agent Chaos failed: {error}", err=True)
        raise typer.Exit(3) from error

    _render_summary(execution)
    raise typer.Exit(execution.exit_code)


def _render_event(event: Event) -> None:
    prefix = f"{event.elapsed_ms / 1000:08.3f}"
    payload = event.payload
    if event.event_type == EventType.OPERATION_OBSERVED and isinstance(
        payload, OperationObservedPayload
    ):
        typer.echo(f"{prefix}  TOOL_CALL      {payload.method} {payload.path}")
    elif event.event_type == EventType.FAULT_INJECTED and isinstance(payload, FaultInjectedPayload):
        typer.echo(f"{prefix}  CHAOS          {payload.fault_type} {payload.parameters}")
    elif event.event_type == EventType.OPERATION_FAILED and isinstance(
        payload, OperationFailedPayload
    ):
        typer.echo(f"{prefix}  TOOL_FAILURE   {payload.failure_kind}")
    elif event.event_type == EventType.RETRY_OBSERVED and isinstance(payload, RetryObservedPayload):
        typer.echo(f"{prefix}  RETRY          attempt={payload.attempt}")
    elif event.event_type == EventType.OPERATION_SUCCEEDED and isinstance(
        payload, OperationSucceededPayload
    ):
        typer.echo(f"{prefix}  SUCCESS        {payload.status_code}")


def _render_summary(execution: RunExecution) -> None:
    report = execution.report
    typer.echo("\nExperiment complete.\n")
    typer.echo(f"Result: {report.result.value}")
    typer.echo(f"Reason: {report.reason_code}\n")
    typer.echo(f"Faults injected:       {report.faults_injected}")
    typer.echo(f"Failed operations:     {report.failed_operations}")
    typer.echo(f"Retries:               {report.retries_observed}")
    typer.echo(f"Successful recovery:   {'yes' if report.recovery.observed else 'no'}")
    typer.echo(f"Duration:              {report.duration_ms / 1000:.2f}s")
    typer.echo(f"\nReport:\n{execution.run_dir / 'report.json'}")


if __name__ == "__main__":
    app()
