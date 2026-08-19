import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from agentchaos.analysis.analyzer import ExperimentResult
from agentchaos.cli import app
from agentchaos.reporting.models import (
    ArtifactReport,
    FaultReport,
    FaultReportV2,
    RecoveryEvidenceReport,
    RecoveryReport,
    RecoveryReportV2,
    Report,
    TimingReport,
    WorkloadReport,
)
from agentchaos.reporting.summary import render_summary
from agentchaos.reporting.writer import write_report
from agentchaos.runtime.orchestrator import RunExecution

runner = CliRunner()


def test_version_commands() -> None:
    assert runner.invoke(app, ["version"]).exit_code == 0
    assert runner.invoke(app, ["--version"]).exit_code == 0


def test_validate_reports_configuration_errors(tmp_path: Path) -> None:
    scenario = tmp_path / "invalid.yaml"
    scenario.write_text("name: missing-everything\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(scenario)])

    assert result.exit_code == 2
    assert "Invalid scenario" in result.output


@pytest.mark.parametrize("command", ["validate", "run"])
@pytest.mark.parametrize("malformed", [b"\xff\xfe", b"schema_version: " + b"9" * 5_000])
def test_scenario_commands_safely_reject_decode_and_scalar_failures(
    tmp_path: Path, command: str, malformed: bytes
) -> None:
    scenario = tmp_path / "invalid.yaml"
    scenario.write_bytes(malformed)

    result = runner.invoke(app, [command, str(scenario)])

    assert result.exit_code == 2
    assert "Invalid scenario" in result.stderr
    assert "9999999999" not in result.output
    assert "\\xff" not in result.output


def test_validate_accepts_example() -> None:
    result = runner.invoke(app, ["validate", "examples/scenarios/api_503_recovery.yaml"])

    assert result.exit_code == 0
    assert "Valid scenario" in result.output


@pytest.mark.parametrize("use_directory", [True, False])
@pytest.mark.parametrize(
    ("experiment_result", "reason", "recovered"),
    [
        (ExperimentResult.PASSED, "BASELINE_SUCCEEDED", False),
        (ExperimentResult.RECOVERED, "RECOVERY_OBSERVED", True),
        (ExperimentResult.FAILED, "RECOVERY_NOT_OBSERVED", False),
    ],
)
def test_inspect_accepts_directory_or_report_for_every_result(
    tmp_path: Path,
    use_directory: bool,
    experiment_result: ExperimentResult,
    reason: str,
    recovered: bool,
) -> None:
    report = _report(result=experiment_result, reason=reason, recovered=recovered)
    report_path = tmp_path / "report.json"
    write_report(report, report_path)

    result = runner.invoke(app, ["inspect", str(tmp_path if use_directory else report_path)])

    assert result.exit_code == 0
    assert result.output == render_summary(report, report_path.resolve())
    assert f"Result: {experiment_result.value}" in result.output
    assert f"Reason: {reason}" in result.output
    assert "Faults injected:       1" in result.output
    assert "Failed operations:     1" in result.output
    assert "Retries:               2" in result.output
    assert f"Successful recovery:   {'yes' if recovered else 'no'}" in result.output
    assert "Duration:              1.23s" in result.output
    assert str(report_path.resolve()) in result.output


def test_inspect_does_not_modify_artifacts(tmp_path: Path) -> None:
    write_report(_report(), tmp_path / "report.json")
    for name, contents in {
        "scenario.yaml": b"scenario bytes\n",
        "events.jsonl": b'{"sensitive":"event"}\n',
        "stdout.log": b"stdout bytes\n",
        "stderr.log": b"stderr bytes\n",
    }.items():
        (tmp_path / name).write_bytes(contents)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    result = runner.invoke(app, ["inspect", str(tmp_path)])

    after = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert result.exit_code == 0
    assert after == before
    assert "sensitive" not in result.output


@pytest.mark.parametrize(
    ("name", "expected_error"),
    [
        ("missing.json", "Report not found."),
        ("missing-run", "Report not found."),
    ],
)
def test_inspect_rejects_missing_report(tmp_path: Path, name: str, expected_error: str) -> None:
    report_input = tmp_path / name
    if name == "missing-run":
        report_input.mkdir()

    result = runner.invoke(app, ["inspect", str(report_input)])

    assert result.exit_code == 2
    assert result.stderr == f"Cannot inspect report: {expected_error}\n"


def test_inspect_rejects_invalid_json(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text('{"diagnostics":["secret"]', encoding="utf-8")

    result = runner.invoke(app, ["inspect", str(report_path)])

    assert result.exit_code == 2
    assert result.stderr == "Cannot inspect report: Report is malformed JSON.\n"
    assert "secret" not in result.output


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ({"unknown": "secret"}, "Report schema is invalid."),
        ({"schema_version": 3}, "Report schema version is unsupported."),
        ({"schema_version": 2}, "Report schema is invalid."),
    ],
)
def test_inspect_rejects_invalid_schema(
    tmp_path: Path, change: dict[str, object], expected_error: str
) -> None:
    payload = _report().model_dump(mode="json") | change
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["inspect", str(report_path)])

    assert result.exit_code == 2
    assert result.stderr == f"Cannot inspect report: {expected_error}\n"
    assert "secret" not in result.output


def test_inspect_rejects_unreadable_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_path = tmp_path / "report.json"
    write_report(_report(), report_path)
    original_read_bytes = Path.read_bytes

    def raise_permission_error(path: Path) -> bytes:
        if path == report_path.resolve():
            raise PermissionError
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", raise_permission_error)

    result = runner.invoke(app, ["inspect", str(report_path)])

    assert result.exit_code == 2
    assert result.stderr == "Cannot inspect report: Report is unreadable.\n"


def test_inspect_sanitizes_unexpected_internal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_: Path) -> tuple[Report, Path]:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("agentchaos.cli.load_report", fail)

    result = runner.invoke(app, ["inspect", "report.json"])

    assert result.exit_code == 3
    assert result.stderr == "Agent Chaos inspect failed: unexpected internal error\n"
    assert "secret internal detail" not in result.output


def test_inspect_interruption_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_: Path) -> tuple[Report, Path]:
        raise KeyboardInterrupt

    monkeypatch.setattr("agentchaos.cli.load_report", interrupt)

    result = runner.invoke(app, ["inspect", "report.json"])

    assert result.exit_code == 130
    assert result.output == ""


def test_run_and_inspect_use_same_summary_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report()
    report_path = tmp_path / "report.json"
    write_report(report, report_path)
    renderer = Mock(return_value="shared summary\n")
    monkeypatch.setattr("agentchaos.cli.render_summary", renderer)

    inspected = runner.invoke(app, ["inspect", str(report_path)])

    async def fake_run_experiment(*args: object, **kwargs: object) -> RunExecution:
        return RunExecution(report=report, run_dir=tmp_path, exit_code=0)

    monkeypatch.setattr("agentchaos.cli.run_experiment", fake_run_experiment)
    live = runner.invoke(app, ["run", "examples/scenarios/no_fault.yaml"])

    assert inspected.exit_code == 0
    assert live.exit_code == 0
    assert renderer.call_args_list == [
        ((report, report_path.resolve()),),
        ((report, report_path.resolve()),),
    ]


def test_summary_renderer_exact_output(tmp_path: Path) -> None:
    report_path = (tmp_path / "report.json").resolve()

    assert render_summary(_report(), report_path) == (
        "\nExperiment complete.\n\n"
        "Result: RECOVERED\n"
        "Reason: RECOVERY_OBSERVED\n\n"
        "Faults injected:       1\n"
        "Failed operations:     1\n"
        "Retries:               2\n"
        "Successful recovery:   yes\n"
        "Duration:              1.23s\n"
        f"\nReport:\n{report_path}\n"
    )


@pytest.mark.parametrize("use_directory", [True, False])
def test_inspect_accepts_schema_two_report(tmp_path: Path, use_directory: bool) -> None:
    report = _report_v2()
    report_path = tmp_path / "report.json"
    write_report(report, report_path)

    result = runner.invoke(app, ["inspect", str(tmp_path if use_directory else report_path)])

    assert result.exit_code == 0
    assert result.output == render_summary(report, report_path.resolve())


def test_inspect_rejects_schema_two_outcome_that_contradicts_schedule(tmp_path: Path) -> None:
    payload = _report_v2().model_dump(mode="json")
    payload["fault"] |= {
        "scheduled_occurrences": [2, 4, 6],
        "schedule_completed": False,
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["inspect", str(report_path)])

    assert result.exit_code == 2
    assert result.stderr == "Cannot inspect report: Report schema is invalid.\n"


def test_schema_two_summary_renderer_exact_output(tmp_path: Path) -> None:
    report_path = (tmp_path / "report.json").resolve()

    assert render_summary(_report_v2(), report_path) == (
        "\nExperiment complete.\n\n"
        "Result: RECOVERED\n"
        "Reason: RECOVERY_OBSERVED\n\n"
        "Faults injected:       2/2\n"
        "Failed operations:     2\n"
        "Retries:               2\n"
        "Successful recoveries: 2/2\n"
        "Duration:              1.23s\n"
        f"\nReport:\n{report_path}\n"
    )


def _report(
    *,
    result: ExperimentResult = ExperimentResult.RECOVERED,
    reason: str = "RECOVERY_OBSERVED",
    recovered: bool = True,
) -> Report:
    return Report(
        run_id="run",
        scenario_name="scenario",
        result=result,
        reason_code=reason,
        duration_ms=1234,
        faults_injected=1,
        operations_observed=3,
        successful_operations=2,
        failed_operations=1,
        retries_observed=2,
        workload_exit_code=0,
        workload=WorkloadReport(
            name="agent", expected_exit_code=0, exit_code=0, timed_out=False, interrupted=False
        ),
        fault=FaultReport(configured=True, type="http_error", injected=True),
        recovery=RecoveryReport(
            observed=recovered,
            failed_operation_id="failed" if recovered else None,
            retry_operation_id="retry" if recovered else None,
            recovery_latency_ms=10 if recovered else None,
        ),
        timing=TimingReport(started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z"),
        artifacts=ArtifactReport(
            scenario="scenario.yaml",
            events="events.jsonl",
            stdout="stdout.log",
            stderr="stderr.log",
            dependency_stdout=None,
            dependency_stderr=None,
            report="report.json",
        ),
        diagnostics=["must not be printed"],
    )


def _report_v2() -> Report:
    return Report(
        schema_version=2,
        run_id="run",
        scenario_name="scenario",
        result=ExperimentResult.RECOVERED,
        reason_code="RECOVERY_OBSERVED",
        duration_ms=1234,
        faults_injected=2,
        operations_observed=4,
        successful_operations=2,
        failed_operations=2,
        retries_observed=2,
        workload_exit_code=0,
        workload=WorkloadReport(
            name="agent", expected_exit_code=0, exit_code=0, timed_out=False, interrupted=False
        ),
        fault=FaultReportV2(
            configured=True,
            type="http_error",
            injected=True,
            scheduled_occurrences=(2, 4),
            completed_occurrences=(2, 4),
            schedule_completed=True,
        ),
        recovery=RecoveryReportV2(
            required=2,
            successful=2,
            evidence=(
                RecoveryEvidenceReport(
                    failed_operation_id="failed-one",
                    retry_operation_id="retry-one",
                    recovery_latency_ms=10,
                ),
                RecoveryEvidenceReport(
                    failed_operation_id="failed-two",
                    retry_operation_id="retry-two",
                    recovery_latency_ms=20,
                ),
            ),
        ),
        timing=TimingReport(started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z"),
        artifacts=ArtifactReport(
            scenario="scenario.yaml",
            events="events.jsonl",
            stdout="stdout.log",
            stderr="stderr.log",
            dependency_stdout=None,
            dependency_stderr=None,
            report="report.json",
        ),
        diagnostics=["must not be printed"],
    )
