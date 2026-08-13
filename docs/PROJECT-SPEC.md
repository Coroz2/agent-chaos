# Agent Chaos v0.1 Project Specification

## Product

Agent Chaos is a local and CI-friendly chaos runner for agent developers. It launches an
opaque subprocess workload, routes one HTTP dependency through a reverse proxy, injects zero
or one deterministic fault, records a structured event stream, and reports whether the
workload passed, recovered, or failed.

The core remains independent of model vendors and agent frameworks. Model-specific wrappers,
semantic task grading, multiple dependencies, and distributed chaos infrastructure are not
part of v0.1.

## Scenario contract

Scenarios use a strict Pydantic model with `schema_version: 1`. They define one HTTP dependency,
one subprocess workload, an optional `http_latency` or `http_error` fault, an occurrence
trigger, expected exit code, and a workload timeout that defaults to 60 seconds. Commands are
argument lists and relative paths resolve from the scenario file.

Managed dependencies are optional. When configured, Agent Chaos starts the command and waits
for a required HTTP health endpoint before launching the proxy and workload. Workloads receive
the proxy through `AGENTCHAOS_PROXY_URL` and an optional scenario-selected environment variable.

## Outcomes

- `PASSED`: a baseline succeeds, or injected latency is tolerated without an observed failure.
- `RECOVERED`: the injected operation fails, a fingerprint-matched retry succeeds, and the
  workload exits with the expected code.
- `FAILED`: execution fails, the fault never triggers, or recovery is not observed.

Retry fingerprints combine method, concrete path, hashed query, and body hash. This is a
documented deterministic heuristic, not semantic agent instrumentation.

## Runtime and artifacts

The Starlette/Uvicorn reverse proxy binds to loopback, forwards with httpx, strips hop-by-hop
headers, does not follow redirects, and buffers payloads up to 10 MiB. HTTP errors are synthetic
JSON responses. Latency races the configured delay against client disconnect and avoids calling
upstream when the client abandons the request.

Each valid run writes a scenario snapshot, append-only `events.jsonl`, workload logs, optional
dependency logs, and an atomic `report.json` under `.agentchaos/runs/<run-id>/`. Events and reports
are versioned public contracts and do not persist request headers, bodies, or raw query values.

## CLI and support

The CLI provides `agentchaos run`, `agentchaos validate`, `agentchaos version`, and
`agentchaos --version`. Passing and recovered experiments exit 0, experiment failures exit 1,
configuration failures exit 2, setup failures exit 3, and interruptions exit 130.

v0.1 supports macOS and Linux. Streaming responses, SSE, WebSockets, CONNECT tunneling, TLS
interception, multiple faults, probabilistic triggers, and non-HTTP adapters are deferred.

