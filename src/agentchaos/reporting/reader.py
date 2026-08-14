"""Read and validate saved reports without touching other run artifacts."""

from pathlib import Path

from pydantic import ValidationError

from agentchaos.reporting.models import Report


class ReportReadError(ValueError):
    """A safe, user-facing saved-report validation error."""


def load_report(input_path: Path) -> tuple[Report, Path]:
    """Resolve and strictly validate a saved report."""
    candidate = input_path / "report.json" if input_path.is_dir() else input_path
    try:
        report_path = candidate.resolve()
        report_bytes = report_path.read_bytes()
    except FileNotFoundError as error:
        raise ReportReadError("Report not found.") from error
    except (OSError, RuntimeError) as error:
        raise ReportReadError("Report is unreadable.") from error

    try:
        report = Report.model_validate_json(report_bytes, strict=True)
    except ValidationError as error:
        errors = error.errors(include_url=False, include_context=False, include_input=False)
        if any(item["type"] == "json_invalid" for item in errors):
            message = "Report is malformed JSON."
        elif any(item["loc"] == ("schema_version",) for item in errors):
            message = "Report schema version is unsupported."
        else:
            message = "Report schema is invalid."
        raise ReportReadError(message) from error

    return report, report_path
