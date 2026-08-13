"""Atomic JSON report writing."""

from pathlib import Path

from agentchaos.reporting.models import Report


def write_report(report: Report, path: Path) -> None:
    """Atomically replace a run report with a complete JSON document."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
