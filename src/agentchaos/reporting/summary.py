"""Shared text rendering for experiment reports."""

from pathlib import Path

from agentchaos.reporting.models import RecoveryReport, RecoveryReportV2, ReportDocument


def render_summary(report: ReportDocument, report_path: Path) -> str:
    """Render the canonical result summary for a validated report."""
    if report.schema_version == 1:
        assert isinstance(report.recovery, RecoveryReport)
        recovery_lines = f"Successful recovery:   {'yes' if report.recovery.observed else 'no'}\n"
    else:
        assert isinstance(report.recovery, RecoveryReportV2)
        recovery_lines = (
            f"Recoveries required:   {report.recovery.required}\n"
            f"Recoveries successful: {report.recovery.successful}\n"
        )
    return (
        "\nExperiment complete.\n\n"
        f"Result: {report.result.value}\n"
        f"Reason: {report.reason_code}\n\n"
        f"Faults injected:       {report.faults_injected}\n"
        f"Failed operations:     {report.failed_operations}\n"
        f"Retries:               {report.retries_observed}\n"
        f"{recovery_lines}"
        f"Duration:              {report.duration_ms / 1000:.2f}s\n"
        f"\nReport:\n{report_path}\n"
    )
