from pathlib import Path

import pytest

from agentchaos.analysis.analyzer import ExperimentResult
from agentchaos.config.loader import load_scenario
from agentchaos.runtime.orchestrator import run_experiment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected_result", "expected_exit"),
    [
        ("no_fault.yaml", ExperimentResult.PASSED, 0),
        ("api_latency_recovery.yaml", ExperimentResult.RECOVERED, 0),
        ("api_503_recovery.yaml", ExperimentResult.RECOVERED, 0),
        ("api_503_failure.yaml", ExperimentResult.FAILED, 1),
        ("http_malformed_json_recovery.yaml", ExperimentResult.RECOVERED, 0),
        ("http_malformed_json_failure.yaml", ExperimentResult.FAILED, 1),
    ],
)
async def test_demo_scenarios_end_to_end(
    tmp_path: Path,
    filename: str,
    expected_result: ExperimentResult,
    expected_exit: int,
) -> None:
    scenario_path = Path("examples/scenarios") / filename
    execution = await run_experiment(
        load_scenario(scenario_path),
        scenario_path.resolve(),
        tmp_path / "runs",
        stream_output=False,
    )

    assert execution.report.result == expected_result
    assert execution.exit_code == expected_exit
    assert (execution.run_dir / "report.json").exists()
    assert (execution.run_dir / "events.jsonl").exists()
    if expected_result == ExperimentResult.RECOVERED:
        assert execution.report.recovery.observed
        assert execution.report.retries_observed >= 1
    if filename == "http_malformed_json_recovery.yaml":
        assert execution.report.reason_code == "RECOVERY_OBSERVED"
        assert execution.report.fault.type == "http_malformed_json"
        assert execution.report.faults_injected == 1
        assert execution.report.failed_operations == 1
        assert execution.report.retries_observed == 1
        assert execution.report.recovery.failed_operation_id is not None
        assert execution.report.recovery.retry_operation_id is not None
    elif filename == "http_malformed_json_failure.yaml":
        assert execution.report.reason_code == "RECOVERY_NOT_OBSERVED"
        assert execution.report.fault.type == "http_malformed_json"
        assert execution.report.faults_injected == 1
        assert execution.report.failed_operations == 1
        assert execution.report.retries_observed == 0
        assert not execution.report.recovery.observed
