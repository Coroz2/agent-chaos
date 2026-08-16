"""End-to-end experiment orchestration."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentchaos.analysis.analyzer import AnalysisResult, ExperimentResult, analyze
from agentchaos.config.models import Scenario
from agentchaos.events.models import (
    DependencyReadyPayload,
    DependencyStartedPayload,
    DependencyStoppedPayload,
    Event,
    InjectorStartedPayload,
    RunCompletedPayload,
    RunErrorPayload,
    RunStartedPayload,
    WorkloadCompletedPayload,
    WorkloadStartedPayload,
)
from agentchaos.events.recorder import EventRecorder
from agentchaos.proxy.server import ChaosProxy
from agentchaos.reporting.models import (
    ArtifactReport,
    FaultReportV2,
    RecoveryEvidenceReport,
    RecoveryReportV2,
    Report,
    TimingReport,
    WorkloadReport,
)
from agentchaos.reporting.writer import write_report
from agentchaos.runtime.process import ManagedProcess, ProcessSpec, merged_environment
from agentchaos.runtime.readiness import DependencyReadinessError, wait_for_http_readiness


@dataclass(frozen=True, slots=True)
class RunExecution:
    report: Report
    run_dir: Path
    exit_code: int


async def run_experiment(
    scenario: Scenario,
    scenario_path: Path,
    output_root: Path,
    event_listener: Callable[[Event], None] | None = None,
    stream_output: bool = True,
) -> RunExecution:
    """Run one validated scenario and always finalize its artifact set."""
    run_id = _new_run_id()
    run_dir = output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    scenario_artifact = run_dir / "scenario.yaml"
    shutil.copy2(scenario_path, scenario_artifact)
    events_path = run_dir / "events.jsonl"
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    dependency_stdout_path = run_dir / "dependency.stdout.log"
    dependency_stderr_path = run_dir / "dependency.stderr.log"
    report_path = run_dir / "report.json"
    stdout_path.touch()
    stderr_path.touch()

    dependency_process: ManagedProcess | None = None
    proxy: ChaosProxy | None = None
    setup_exit_code = 0

    async with EventRecorder(events_path, run_id, listener=event_listener) as recorder:
        await recorder.emit("orchestrator", RunStartedPayload(scenario_name=scenario.name))
        try:
            if scenario.dependency.start is not None:
                start = scenario.dependency.start
                dependency_stdout_path.touch()
                dependency_stderr_path.touch()
                dependency_process = ManagedProcess(
                    ProcessSpec(
                        command=start.command,
                        cwd=scenario.resolve_path(scenario_path, start.cwd),
                        env=merged_environment(start.env),
                        stdout_path=dependency_stdout_path,
                        stderr_path=dependency_stderr_path,
                        label="dependency",
                        stream_output=False,
                    )
                )
                try:
                    await dependency_process.start()
                    await recorder.emit(
                        "dependency",
                        DependencyStartedPayload(
                            base_url=str(scenario.dependency.base_url),
                            pid=dependency_process.pid,
                        ),
                    )
                    readiness = start.readiness
                    readiness_url = str(scenario.dependency.base_url).rstrip("/") + readiness.path
                    await wait_for_http_readiness(
                        dependency_process,
                        readiness_url,
                        readiness.timeout_seconds,
                    )
                    await recorder.emit(
                        "dependency",
                        DependencyReadyPayload(
                            base_url=str(scenario.dependency.base_url),
                            readiness_path=readiness.path,
                        ),
                    )
                except (OSError, DependencyReadinessError) as error:
                    setup_exit_code = 3
                    await recorder.emit(
                        "orchestrator",
                        RunErrorPayload(
                            reason_code="DEPENDENCY_START_FAILED",
                            message=str(error),
                        ),
                    )
                    raise _RunAborted from error

            proxy = ChaosProxy(str(scenario.dependency.base_url), scenario.fault, recorder)
            try:
                proxy_url = await proxy.start()
            except Exception as error:
                setup_exit_code = 3
                await recorder.emit(
                    "orchestrator",
                    RunErrorPayload(reason_code="PROXY_START_FAILED", message=str(error)),
                )
                raise _RunAborted from error
            await recorder.emit(
                "injector",
                InjectorStartedPayload(
                    proxy_url=proxy_url,
                    upstream_url=str(scenario.dependency.base_url),
                ),
            )

            workload_env = merged_environment(scenario.workload.env)
            workload_env["AGENTCHAOS_PROXY_URL"] = proxy_url
            workload_env["AGENTCHAOS_RUN_ID"] = run_id
            if scenario.workload.proxy_url_env is not None:
                workload_env[scenario.workload.proxy_url_env] = proxy_url
            workload = ManagedProcess(
                ProcessSpec(
                    command=scenario.workload.command,
                    cwd=scenario.resolve_path(scenario_path, scenario.workload.cwd),
                    env=workload_env,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    label=scenario.workload.name or "workload",
                    stream_output=stream_output,
                )
            )
            try:
                await workload.start()
            except OSError as error:
                setup_exit_code = 3
                await recorder.emit(
                    "orchestrator",
                    RunErrorPayload(reason_code="WORKLOAD_SPAWN_FAILED", message=str(error)),
                )
                raise _RunAborted from error
            await recorder.emit(
                "workload",
                WorkloadStartedPayload(
                    name=scenario.workload.name or scenario.workload.command[0],
                    pid=workload.pid,
                ),
            )
            workload_result = await workload.wait(scenario.timeout_seconds)
            await recorder.emit(
                "workload",
                WorkloadCompletedPayload(
                    exit_code=workload_result.exit_code,
                    timed_out=workload_result.timed_out,
                    interrupted=workload_result.interrupted,
                    duration_ms=workload_result.duration_ms,
                    error=workload_result.error,
                ),
            )
        except _RunAborted:
            pass
        except Exception as error:
            setup_exit_code = 3
            await recorder.emit(
                "orchestrator",
                RunErrorPayload(reason_code="INTERNAL_ERROR", message=str(error)),
            )
        finally:
            if proxy is not None:
                await proxy.stop()
            if dependency_process is not None:
                await dependency_process.stop()
                await recorder.emit(
                    "dependency",
                    DependencyStoppedPayload(exit_code=dependency_process.returncode),
                )

        preliminary = analyze(scenario, recorder.events)
        await recorder.emit(
            "orchestrator",
            RunCompletedPayload(
                result=preliminary.result.value,
                reason_code=preliminary.reason_code,
                duration_ms=preliminary.duration_ms,
            ),
        )
        analysis = analyze(scenario, recorder.events)
        report = _build_report(
            run_id,
            scenario,
            analysis,
            recorder.events,
            managed_dependency=scenario.dependency.start is not None,
        )
        write_report(report, report_path)

    return RunExecution(
        report=report,
        run_dir=run_dir,
        exit_code=_exit_code(report.result, report.reason_code, setup_exit_code),
    )


class _RunAborted(Exception):
    pass


def _build_report(
    run_id: str,
    scenario: Scenario,
    analysis: AnalysisResult,
    events: tuple[Event, ...],
    managed_dependency: bool,
) -> Report:
    started_at = events[0].timestamp
    completed_at = events[-1].timestamp
    return Report(
        schema_version=2,
        run_id=run_id,
        scenario_name=scenario.name,
        result=analysis.result,
        reason_code=analysis.reason_code,
        duration_ms=analysis.duration_ms,
        faults_injected=analysis.faults_injected,
        operations_observed=analysis.operations_observed,
        successful_operations=analysis.successful_operations,
        failed_operations=analysis.failed_operations,
        retries_observed=analysis.retries_observed,
        workload_exit_code=analysis.workload_exit_code,
        workload=WorkloadReport(
            name=scenario.workload.name or scenario.workload.command[0],
            expected_exit_code=scenario.success.exit_code,
            exit_code=analysis.workload_exit_code,
            timed_out=analysis.workload_timed_out,
            interrupted=analysis.workload_interrupted,
        ),
        fault=FaultReportV2(
            configured=scenario.fault is not None,
            type=None if scenario.fault is None else scenario.fault.type,
            injected=analysis.faults_injected > 0,
            scheduled_occurrences=analysis.scheduled_occurrences,
            completed_occurrences=analysis.completed_occurrences,
            schedule_completed=(analysis.completed_occurrences == analysis.scheduled_occurrences),
        ),
        recovery=RecoveryReportV2(
            required=analysis.recoveries_required,
            successful=analysis.recoveries_successful,
            evidence=tuple(
                RecoveryEvidenceReport(
                    failed_operation_id=row.failed_operation_id,
                    successful_retry_operation_id=row.successful_retry_operation_id,
                    recovery_latency_ms=row.recovery_latency_ms,
                )
                for row in analysis.recovery_evidence
            ),
        ),
        timing=TimingReport(started_at=started_at, completed_at=completed_at),
        artifacts=ArtifactReport(
            scenario="scenario.yaml",
            events="events.jsonl",
            stdout="stdout.log",
            stderr="stderr.log",
            dependency_stdout="dependency.stdout.log" if managed_dependency else None,
            dependency_stderr="dependency.stderr.log" if managed_dependency else None,
            report="report.json",
        ),
        diagnostics=list(analysis.diagnostics),
    )


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _exit_code(result: ExperimentResult, reason_code: str, setup_exit_code: int) -> int:
    if reason_code == "INTERRUPTED":
        return 130
    if setup_exit_code:
        return setup_exit_code
    return 0 if result in {ExperimentResult.PASSED, ExperimentResult.RECOVERED} else 1
