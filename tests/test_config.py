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

RATE_LIMIT_SCENARIO = VALID_SCENARIO.replace(
    "type: http_error",
    "type: http_rate_limit",
).replace("  status_code: 503\n", "  retry_after_seconds: 1\n")

MALFORMED_JSON_SCENARIO = VALID_SCENARIO.replace(
    "type: http_error", "type: http_malformed_json"
).replace("  status_code: 503\n", "")

DISCONNECT_SCENARIO = VALID_SCENARIO.replace("type: http_error", "type: http_disconnect").replace(
    "  status_code: 503\n", ""
)


def scenario_for_fault_type(fault_type: str) -> str:
    scenario = VALID_SCENARIO.replace("type: http_error", f"type: {fault_type}")
    if fault_type == "http_latency":
        return scenario.replace("status_code: 503", "latency_ms: 50")
    if fault_type == "http_rate_limit":
        return scenario.replace("status_code: 503", "retry_after_seconds: 1")
    if fault_type in {"http_malformed_json", "http_disconnect"}:
        return scenario.replace("  status_code: 503\n", "")
    return scenario


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
    assert scenario.fault.trigger.schedule == (2,)


@pytest.mark.parametrize(
    "fault_type",
    [
        "http_latency",
        "http_error",
        "http_rate_limit",
        "http_malformed_json",
        "http_disconnect",
    ],
)
@pytest.mark.parametrize("occurrences", ["[2]", "[2, 4]"])
def test_all_faults_accept_occurrence_schedules(
    tmp_path: Path, fault_type: str, occurrences: str
) -> None:
    content = scenario_for_fault_type(fault_type).replace(
        "occurrence: 2", f"occurrences: {occurrences}"
    )

    scenario = load_scenario(write_scenario(tmp_path, content))

    assert scenario.fault is not None
    assert scenario.fault.trigger.schedule == tuple(
        int(value) for value in occurrences[1:-1].split(", ")
    )


@pytest.mark.parametrize(
    "trigger",
    [
        "{}",
        "{occurrence: 2, occurrences: [2, 4]}",
        "{occurrences: []}",
        "{occurrences: [2, 2]}",
        "{occurrences: [4, 2]}",
        "{occurrences: [0, 2]}",
        "{occurrences: [-1, 2]}",
        "{occurrences: [true, 2]}",
        "{occurrences: [1.5, 2]}",
        "{occurrence: true}",
        "{occurrence: 2.0}",
        "{occurrences: [2], unknown: 3}",
    ],
)
def test_occurrence_trigger_rejects_invalid_forms(tmp_path: Path, trigger: str) -> None:
    invalid = VALID_SCENARIO.replace("trigger:\n    occurrence: 2", f"trigger: {trigger}")

    with pytest.raises(ScenarioLoadError):
        load_scenario(write_scenario(tmp_path, invalid))


@pytest.mark.parametrize("retry_after_seconds", [0, 1, 86400])
def test_rate_limit_scenario_accepts_non_negative_integer(
    tmp_path: Path, retry_after_seconds: int
) -> None:
    scenario = load_scenario(
        write_scenario(
            tmp_path,
            RATE_LIMIT_SCENARIO.replace(
                "retry_after_seconds: 1",
                f"retry_after_seconds: {retry_after_seconds}",
            ),
        )
    )

    assert scenario.fault is not None
    assert scenario.fault.type == "http_rate_limit"
    assert scenario.fault.retry_after_seconds == retry_after_seconds


@pytest.mark.parametrize(
    "invalid_fault",
    [
        "  retry_after_seconds: -1\n",
        "",
        "  retry_after_seconds: 1\n  status_code: 429\n",
        "  retry_after_seconds: 1\n  custom_header: unsafe\n",
    ],
)
def test_rate_limit_scenario_rejects_invalid_or_mixed_fields(
    tmp_path: Path, invalid_fault: str
) -> None:
    invalid = RATE_LIMIT_SCENARIO.replace("  retry_after_seconds: 1\n", invalid_fault)

    with pytest.raises(ScenarioLoadError):
        load_scenario(write_scenario(tmp_path, invalid))


def test_unknown_fault_discriminator_fails(tmp_path: Path) -> None:
    invalid = RATE_LIMIT_SCENARIO.replace("http_rate_limit", "rate_limit")

    with pytest.raises(ScenarioLoadError, match="union_tag_invalid"):
        load_scenario(write_scenario(tmp_path, invalid))


def test_http_malformed_json_scenario_accepts_only_common_fault_fields(tmp_path: Path) -> None:
    scenario = load_scenario(write_scenario(tmp_path, MALFORMED_JSON_SCENARIO))

    assert scenario.fault is not None
    assert scenario.fault.type == "http_malformed_json"


@pytest.mark.parametrize(
    "extra_field",
    [
        "body: configurable",
        "status_code: 201",
        "headers: {X-Test: configurable}",
    ],
)
def test_http_malformed_json_rejects_fault_specific_fields(
    tmp_path: Path, extra_field: str
) -> None:
    invalid = MALFORMED_JSON_SCENARIO.replace(
        "    occurrence: 2", f"    occurrence: 2\n  {extra_field}"
    )

    with pytest.raises(ScenarioLoadError, match="extra_forbidden"):
        load_scenario(write_scenario(tmp_path, invalid))


def test_legacy_malformed_json_discriminator_is_rejected(tmp_path: Path) -> None:
    invalid = MALFORMED_JSON_SCENARIO.replace("http_malformed_json", "malformed_json")

    with pytest.raises(ScenarioLoadError, match="union_tag_invalid"):
        load_scenario(write_scenario(tmp_path, invalid))


def test_http_disconnect_scenario_accepts_only_common_fault_fields(tmp_path: Path) -> None:
    scenario = load_scenario(write_scenario(tmp_path, DISCONNECT_SCENARIO))

    assert scenario.fault is not None
    assert scenario.fault.type == "http_disconnect"


@pytest.mark.parametrize(
    "extra_field",
    [
        "latency_ms: 50",
        "status_code: 503",
        "retry_after_seconds: 1",
        "body: configurable",
        "headers: {X-Test: configurable}",
    ],
)
def test_http_disconnect_rejects_fault_specific_fields(tmp_path: Path, extra_field: str) -> None:
    invalid = DISCONNECT_SCENARIO.replace(
        "    occurrence: 2", f"    occurrence: 2\n  {extra_field}"
    )

    with pytest.raises(ScenarioLoadError, match="extra_forbidden"):
        load_scenario(write_scenario(tmp_path, invalid))


@pytest.mark.parametrize("alias", ["disconnect", "http_disconnection"])
def test_http_disconnect_aliases_are_rejected(tmp_path: Path, alias: str) -> None:
    invalid = DISCONNECT_SCENARIO.replace("http_disconnect", alias)

    with pytest.raises(ScenarioLoadError, match="union_tag_invalid"):
        load_scenario(write_scenario(tmp_path, invalid))


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


def test_non_utf8_scenario_is_a_safe_load_error(tmp_path: Path) -> None:
    path = tmp_path / "scenario.yaml"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(ScenarioLoadError, match="UTF-8") as raised:
        load_scenario(path)

    assert "\\xff" not in str(raised.value)


def test_yaml_scalar_conversion_value_error_is_normalized(tmp_path: Path) -> None:
    oversized_integer = "9" * 5_000
    path = write_scenario(tmp_path, f"schema_version: {oversized_integer}\n")

    with pytest.raises(ScenarioLoadError, match="invalid YAML value") as raised:
        load_scenario(path)

    assert oversized_integer not in str(raised.value)
