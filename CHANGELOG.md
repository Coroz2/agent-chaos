# Changelog

All notable changes to Agent Chaos are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-13

### Added

- Strict, versioned YAML scenarios for one subprocess workload and one HTTP dependency.
- Deterministic HTTP latency and HTTP error injection with occurrence-based triggers.
- A loopback reverse proxy that forwards ordinary HTTP traffic without framework-specific agent
  instrumentation.
- Behavioral recovery detection using request fingerprints and successful matching retries.
- Structured, sequence-ordered JSON Lines events and atomic JSON reports.
- `PASSED`, `RECOVERED`, and `FAILED` outcomes with stable machine-readable reason codes.
- Managed local dependencies, readiness checks, process-group cleanup, workload timeouts, and
  captured logs.
- `agentchaos run`, `agentchaos validate`, and version/help commands for local and CI use.
- Offline baseline, latency-recovery, HTTP-503-recovery, and deliberate-failure demos.
- Linux and macOS CI coverage across Python 3.12, 3.13, and 3.14 where supported.

### Limitations

- Supports one HTTP dependency and zero or one fault per scenario.
- Requires workloads to accept a proxy base URL through environment configuration.
- Buffers HTTP request and response bodies up to 10 MiB.
- Does not support streaming, SSE, WebSockets, CONNECT, TLS interception, probabilistic triggers,
  multiple simultaneous faults, or semantic task grading.
- Retry classification is a deterministic black-box heuristic rather than proof of workload intent.

[Unreleased]: https://github.com/Coroz2/agent-chaos/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Coroz2/agent-chaos/releases/tag/v0.1.0

