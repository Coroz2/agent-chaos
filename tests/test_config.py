from pathlib import Path

import pytest

from agentchaos.config.loader import ScenarioLoadError, load_scenario

VALID_SCENARIO = """
schema_version: 1
name: valid
dependency:
  type: http
  base_url: http://127.0.0.1:9000
workload:
  command: [python, agent.py]
  proxy_url_env: API_URL
fault:
  type: http_error
  target:
    method: get
    path: /customer/*
  trigger:
    occurrence: 2
  status_code: 503
success:
  exit_code: 0
"""


def write_scenario(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "scenario.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_valid_scenario_parses_and_normalizes(tmp_path: Path) -> None:
    scenario = load_scenario(write_scenario(tmp_path, VALID_SCENARIO))

    assert scenario.name == "valid"
    assert scenario.timeout_seconds == 60
    assert scenario.fault is not None
    assert scenario.fault.target.method == "GET"


@pytest.mark.parametrize(
    "replacement",
    [
        "status_code: 399",
        "occurrence: 0",
        "path: customer/*",
        "command: []",
        "proxy_url_env: API-URL",
        "timeout_seconds: 0",
    ],
)
def test_invalid_scenario_fields_fail(tmp_path: Path, replacement: str) -> None:
    substitutions = {
        "status_code: 399": ("status_code: 503", replacement),
        "occurrence: 0": ("occurrence: 2", replacement),
        "path: customer/*": ("path: /customer/*", replacement),
        "command: []": ("command: [python, agent.py]", replacement),
        "proxy_url_env: API-URL": ("proxy_url_env: API_URL", replacement),
        "timeout_seconds: 0": (
            "success:\n  exit_code: 0",
            "timeout_seconds: 0\nsuccess:\n  exit_code: 0",
        ),
    }
    old, new = substitutions[replacement]
    with pytest.raises(ScenarioLoadError):
        load_scenario(write_scenario(tmp_path, VALID_SCENARIO.replace(old, new)))


def test_unknown_fields_fail(tmp_path: Path) -> None:
    with pytest.raises(ScenarioLoadError, match="extra_forbidden"):
        load_scenario(write_scenario(tmp_path, VALID_SCENARIO + "unknown: true\n"))


def test_invalid_fault_union_fails(tmp_path: Path) -> None:
    invalid = VALID_SCENARIO.replace(
        "status_code: 503",
        "latency_ms: 50\n  status_code: 503",
    ).replace("http_error", "http_latency")
    with pytest.raises(ScenarioLoadError):
        load_scenario(write_scenario(tmp_path, invalid))


def test_non_mapping_yaml_fails(tmp_path: Path) -> None:
    with pytest.raises(ScenarioLoadError, match="YAML mapping"):
        load_scenario(write_scenario(tmp_path, "- not\n- a\n- mapping\n"))
