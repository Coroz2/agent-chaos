from pathlib import Path

import pytest

from agentchaos.analysis.analyzer import ExperimentResult
from agentchaos.config.loader import load_scenario
from agentchaos.runtime.orchestrator import run_experiment


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
    ),
    [
        ("no_fault.yaml", ExperimentResult.PASSED, "BASELINE_SUCCEEDED", 0, 0, 0, 0),
        (
            "api_latency_recovery.yaml",
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
            0,
            1,
            1,
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
        ),
        (
            "api_503_failure.yaml",
            ExperimentResult.FAILED,
            "WORKLOAD_EXIT_CODE_MISMATCH",
            1,
            1,
            1,
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
        ),
        (
            "api_429_failure.yaml",
            ExperimentResult.FAILED,
            "RECOVERY_NOT_OBSERVED",
            1,
            1,
            1,
            0,
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
) -> None:
    scenario_path = Path("examples/scenarios") / filename
    execution = await run_experiment(
        load_scenario(scenario_path),
        scenario_path.resolve(),
        tmp_path / "runs",
        stream_output=False,
    )

    assert execution.report.result == expected_result
    assert execution.report.reason_code == expected_reason
    assert execution.exit_code == expected_exit
    assert execution.report.faults_injected == expected_faults
    assert execution.report.failed_operations == expected_failures
    assert execution.report.retries_observed == expected_retries
    assert (execution.run_dir / "report.json").exists()
    assert (execution.run_dir / "events.jsonl").exists()
    if expected_result == ExperimentResult.RECOVERED:
        assert execution.report.recovery.observed
        assert execution.report.retries_observed >= 1
