import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentchaos.analysis.analyzer import ExperimentResult
from agentchaos.cli import app
from agentchaos.config.loader import load_scenario
from agentchaos.config.models import Scenario
from agentchaos.runtime.orchestrator import run_experiment

runner = CliRunner()


@pytest.mark.asyncio
async def test_lifecycle_events_sanitize_dependency_url_secrets(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("user-authored scenario\n", encoding="utf-8")
    scenario = Scenario.model_validate(
        {
            "schema_version": 1,
            "name": "safe lifecycle evidence",
            "dependency": {
                "type": "http",
                "base_url": (
                    "http://alice:super-secret@127.0.0.1:9000/root?token=raw-query-secret"
                ),
            },
            "workload": {"command": [sys.executable, "-c", "raise SystemExit(0)"]},
            "success": {"exit_code": 0},
        }
    )

    execution = await run_experiment(
        scenario,
        scenario_path,
        tmp_path / "runs",
        stream_output=False,
    )

    generated = "\n".join(
        [
            (execution.run_dir / "events.jsonl").read_text(encoding="utf-8"),
            (execution.run_dir / "report.json").read_text(encoding="utf-8"),
        ]
    )
    assert execution.report.result == ExperimentResult.PASSED
    assert "http://127.0.0.1:9000/root" in generated
    for secret in ("alice", "super-secret", "raw-query-secret"):
        assert secret not in generated


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "filename",
        "expected_result",
        "expected_reason",
        "expected_exit",
        "expected_faults",
        "expected_failures",
        "expected_retries",
        "expected_schedule",
        "expected_completed",
        "expected_recoveries",
    ),
    [
        (
            "no_fault.yaml",
            ExperimentResult.PASSED,
            "BASELINE_SUCCEEDED",
            0,
            0,
            0,
            0,
            [],
            [],
            0,
        ),
        (
            "api_latency_recovery.yaml",
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
            0,
            1,
            1,
            1,
            [2],
            [2],
            1,
        ),
        (
            "api_503_recovery.yaml",
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
            0,
            1,
            1,
            1,
            [2],
            [2],
            1,
        ),
        (
            "api_503_failure.yaml",
            ExperimentResult.FAILED,
            "WORKLOAD_EXIT_CODE_MISMATCH",
            1,
            1,
            1,
            0,
            [2],
            [2],
            0,
        ),
        (
            "api_429_recovery.yaml",
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
            0,
            1,
            1,
            1,
            [2],
            [2],
            1,
        ),
        (
            "api_429_failure.yaml",
            ExperimentResult.FAILED,
            "RECOVERY_NOT_OBSERVED",
            1,
            1,
            1,
            0,
            [2],
            [2],
            0,
        ),
        (
            "http_malformed_json_recovery.yaml",
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
            0,
            1,
            1,
            1,
            [2],
            [2],
            1,
        ),
        (
            "http_malformed_json_failure.yaml",
            ExperimentResult.FAILED,
            "RECOVERY_NOT_OBSERVED",
            1,
            1,
            1,
            0,
            [2],
            [2],
            0,
        ),
        (
            "http_disconnect_recovery.yaml",
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
            0,
            1,
            1,
            1,
            [2],
            [2],
            1,
        ),
        (
            "http_disconnect_failure.yaml",
            ExperimentResult.FAILED,
            "RECOVERY_NOT_OBSERVED",
            1,
            1,
            1,
            0,
            [2],
            [2],
            0,
        ),
        (
            "api_503_schedule_recovery.yaml",
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
            0,
            2,
            2,
            2,
            [2, 4],
            [2, 4],
            2,
        ),
        (
            "api_503_schedule_incomplete.yaml",
            ExperimentResult.FAILED,
            "FAULT_SCHEDULE_INCOMPLETE",
            1,
            1,
            1,
            1,
            [2, 4],
            [2],
            1,
        ),
    ],
)
async def test_demo_scenarios_end_to_end(
    tmp_path: Path,
    filename: str,
    expected_result: ExperimentResult,
    expected_reason: str,
    expected_exit: int,
    expected_faults: int,
    expected_failures: int,
    expected_retries: int,
    expected_schedule: list[int],
    expected_completed: list[int],
    expected_recoveries: int,
) -> None:
    scenario_path = Path("examples/scenarios") / filename
    execution = await run_experiment(
        load_scenario(scenario_path),
        scenario_path.resolve(),
        tmp_path / "runs",
        stream_output=False,
    )

    assert execution.report.result == expected_result, execution.report.model_dump_json(indent=2)
    assert execution.report.reason_code == expected_reason
    assert execution.exit_code == expected_exit
    assert execution.report.faults_injected == expected_faults
    assert execution.report.failed_operations == expected_failures
    assert execution.report.retries_observed == expected_retries
    assert execution.report.schema_version == 2
    assert execution.report.fault.scheduled_occurrences == tuple(expected_schedule)
    assert execution.report.fault.completed_occurrences == tuple(expected_completed)
    assert execution.report.fault.schedule_completed == (expected_completed == expected_schedule)
    assert execution.report.recovery.required == expected_failures
    assert execution.report.recovery.successful == expected_recoveries
    assert len(execution.report.recovery.evidence) == expected_failures
    for index, evidence in enumerate(execution.report.recovery.evidence):
        assert evidence.failed_operation_id
        if index < expected_recoveries:
            assert evidence.retry_operation_id is not None
            assert evidence.recovery_latency_ms is not None
        else:
            assert evidence.retry_operation_id is None
            assert evidence.recovery_latency_ms is None
    assert (execution.run_dir / "report.json").exists()
    assert (execution.run_dir / "events.jsonl").exists()
    if expected_result == ExperimentResult.RECOVERED:
        assert execution.report.recovery.required == execution.report.recovery.successful
        assert execution.report.retries_observed >= 1
    if filename == "http_malformed_json_recovery.yaml":
        assert execution.report.reason_code == "RECOVERY_OBSERVED"
        assert execution.report.fault.type == "http_malformed_json"
        assert execution.report.faults_injected == 1
        assert execution.report.failed_operations == 1
        assert execution.report.retries_observed == 1
    elif filename == "http_malformed_json_failure.yaml":
        assert execution.report.reason_code == "RECOVERY_NOT_OBSERVED"
        assert execution.report.fault.type == "http_malformed_json"
        assert execution.report.faults_injected == 1
        assert execution.report.failed_operations == 1
        assert execution.report.retries_observed == 0
    elif filename == "http_disconnect_recovery.yaml":
        assert execution.report.reason_code == "RECOVERY_OBSERVED"
        assert execution.report.fault.type == "http_disconnect"
        assert execution.report.faults_injected == 1
        assert execution.report.failed_operations == 1
        assert execution.report.retries_observed == 1
        inspection = runner.invoke(app, ["inspect", str(execution.run_dir)])
        assert inspection.exit_code == 0
        assert "Result: RECOVERED" in inspection.output
        assert "Reason: RECOVERY_OBSERVED" in inspection.output
    elif filename == "http_disconnect_failure.yaml":
        assert execution.report.reason_code == "RECOVERY_NOT_OBSERVED"
        assert execution.report.fault.type == "http_disconnect"
        assert execution.report.faults_injected == 1
        assert execution.report.failed_operations == 1
        assert execution.report.retries_observed == 0
        inspection = runner.invoke(app, ["inspect", str(execution.run_dir)])
        assert inspection.exit_code == 0
        assert "Result: FAILED" in inspection.output
        assert "Reason: RECOVERY_NOT_OBSERVED" in inspection.output
    elif filename == "api_503_schedule_recovery.yaml":
        inspection = runner.invoke(app, ["inspect", str(execution.run_dir)])
        assert inspection.exit_code == 0
        assert "Faults injected:       2/2" in inspection.output
        assert "Successful recoveries: 2/2" in inspection.output
    elif filename == "api_503_schedule_incomplete.yaml":
        inspection = runner.invoke(app, ["inspect", str(execution.run_dir)])
        assert inspection.exit_code == 0
        assert "Result: FAILED" in inspection.output
        assert "Reason: FAULT_SCHEDULE_INCOMPLETE" in inspection.output
        assert "Faults injected:       1/2" in inspection.output
        assert "Successful recoveries: 1/1" in inspection.output
