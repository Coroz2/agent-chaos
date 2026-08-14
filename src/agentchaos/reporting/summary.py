"""Shared text rendering for experiment reports."""

from pathlib import Path

from agentchaos.reporting.models import Report


def render_summary(report: Report, report_path: Path) -> str:
    """Render the canonical result summary for a validated report."""
    recovery = "yes" if report.recovery.observed else "no"
    return (
        "\nExperiment complete.\n\n"
        f"Result: {report.result.value}\n"
        f"Reason: {report.reason_code}\n\n"
        f"Faults injected:       {report.faults_injected}\n"
        f"Failed operations:     {report.failed_operations}\n"
        f"Retries:               {report.retries_observed}\n"
        f"Successful recovery:   {recovery}\n"
        f"Duration:              {report.duration_ms / 1000:.2f}s\n"
        f"\nReport:\n{report_path}\n"
    )
