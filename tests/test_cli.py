from pathlib import Path

from typer.testing import CliRunner

from agentchaos.cli import app

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


def test_validate_accepts_example() -> None:
    result = runner.invoke(app, ["validate", "examples/scenarios/api_503_recovery.yaml"])

    assert result.exit_code == 0
    assert "Valid scenario" in result.output
