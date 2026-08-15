# Changelog

All notable changes to Agent Chaos are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-08-15

### Added

- Deterministic `http_disconnect` injection with structured recovery evidence and managed offline
  recovery and no-recovery examples.

### Fixed

- Increased the managed HTTP-disconnect recovery example's client timeout so connection teardown
  and retry remain reliable on slower macOS CI runners.

## [0.2.0] - 2026-08-13

### Added

- Read-only `agentchaos inspect` support for strictly validated saved reports, using the same
  result summary as live runs.
- Deterministic HTTP 429 rate-limit injection with integer `Retry-After` values, structured
  recovery evidence, and managed offline recovery and no-recovery examples.
- Deterministic `http_malformed_json` injection with a fixed invalid JSON response, explicit
  recovery evidence, and managed recovery and no-recovery demos.

### Changed

- Modularized deterministic HTTP fault execution behind a private typed executor abstraction while
  preserving existing v0.1 behavior and public contracts.

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

[Unreleased]: https://github.com/Coroz2/agent-chaos/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Coroz2/agent-chaos/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Coroz2/agent-chaos/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Coroz2/agent-chaos/releases/tag/v0.1.0
