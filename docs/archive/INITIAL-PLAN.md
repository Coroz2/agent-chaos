You are building the initial version of an open-source project called **Agent Chaos**.

## Project idea

Agent Chaos is:

> **Chaos engineering for autonomous AI agents.**

Modern AI agents depend on model APIs, MCP servers, databases, HTTP APIs, filesystems, browsers, and other tools. These dependencies can fail in production in unpredictable ways.

Agent Chaos should let developers deliberately inject failures into an agent's environment and measure whether the agent successfully detects, handles, retries, replans around, or fails because of those faults.

Long term, the project may support failures involving:

* model APIs
* MCP servers
* HTTP APIs
* PostgreSQL and other databases
* GitHub
* network connectivity
* filesystems
* process crashes
* malformed tool responses
* latency
* rate limiting
* partial outages

Do **not** attempt to implement all of this now.

We are building the first technically sound vertical slice.

---

# Initial MVP

The MVP should allow a developer to:

1. Define an experiment in YAML.
2. Run an agent-like workload.
3. Inject one or more controlled faults into an HTTP dependency.
4. Record everything that happens during the experiment.
5. Determine whether the workload successfully recovered.
6. Produce a human-readable terminal summary.
7. Save a machine-readable JSON report.

The primary command should eventually look approximately like:

```bash
agentchaos run scenarios/api_timeout.yaml
```

Example terminal output:

```text
Agent Chaos

Experiment: api-timeout-recovery
Target: demo-agent

Starting workload...

00:00.214  TOOL_CALL     GET /customer/123
00:00.229  SUCCESS       200

00:01.103  CHAOS         Injecting 3000ms latency
00:04.109  TOOL_FAILURE  timeout

00:04.521  RETRY         attempt=2
00:04.548  SUCCESS       200

Experiment complete.

Result: RECOVERED

Faults injected:       1
Failed operations:     1
Retries:               1
Successful recovery:   yes
Duration:              4.8s

Report:
.agentchaos/runs/<run-id>/report.json
```

Exact formatting can differ. Focus on clean architecture and useful information.

---

# Important design principle

**Agent Chaos should not be tied to LangChain, CrewAI, OpenAI Agents SDK, or any particular agent framework.**

The core system should treat the workload as a black box whenever possible.

Eventually users should be able to test many different agent implementations.

Design extension points now, but only implement what is necessary for the MVP.

---

# Technology

Use:

* Python 3.12+
* `uv` for dependency/project management
* `Typer` for the CLI
* `Pydantic` for configuration and event models
* `PyYAML` or equivalent for scenario parsing
* `asyncio` for orchestration
* `httpx` where HTTP clients are required
* `pytest` for testing

For the local HTTP services/proxy, choose a lightweight Python approach such as FastAPI/Starlette or another reasonable async implementation.

Do not introduce Kubernetes, Redis, Kafka, Celery, or other infrastructure yet.

Keep the project easy to run locally.

---

# Architecture

Create clean modules around the following concepts.

## 1. Scenario

A scenario describes:

* experiment name
* workload
* fault(s)
* success criteria
* optional timeout

Example:

```yaml
name: api-timeout-recovery

workload:
  command:
    - python
    - examples/demo_agent.py

faults:
  - type: http_latency
    target:
      path: /customer/*
    trigger:
      occurrence: 2
    parameters:
      latency_ms: 3000

success:
  exit_code: 0

timeout_seconds: 30
```

You may adjust this schema if necessary, but keep it simple and readable.

Validate configuration using Pydantic.

---

# 2. Workload Runner

Implement a workload abstraction.

For the first version, support running a local subprocess.

Example:

```python
runner = SubprocessRunner(...)
result = await runner.run()
```

Capture:

* stdout
* stderr
* exit code
* start time
* end time

The architecture should make future runners possible, such as:

* Docker workload
* Python callable
* remote agent
* Kubernetes workload

Do not implement those now.

---

# 3. Fault Injection Engine

Create a generic abstraction similar to:

```python
class FaultInjector(ABC):
    async def start(self): ...
    async def stop(self): ...
```

or another clean interface.

The first implementation should operate on HTTP traffic.

Implement at least these fault types:

### HTTP latency

Delay a matching request by a configured duration.

Example:

```yaml
type: http_latency
latency_ms: 3000
```

### HTTP error

Return a configured HTTP failure.

Example:

```yaml
type: http_error
status_code: 503
```

If the architecture naturally allows it without significant complexity, also support:

```text
timeout
connection close/reset
malformed JSON
429 rate limit
```

But these are secondary.

The initial project is successful with reliable latency and HTTP-error injection.

---

# 4. Trigger System

Faults should not necessarily happen on every request.

Implement a simple trigger abstraction.

For MVP, support:

```yaml
trigger:
  occurrence: 2
```

Meaning:

> inject this fault the second time a matching operation occurs.

Design this so future triggers could include:

```text
after_seconds
probability
during_window
after_event
every_n_requests
```

Do not implement those unless trivial.

---

# 5. Event System

This is important.

Every significant action should produce a structured event.

Define an event model containing information such as:

```text
event_id
run_id
timestamp
event_type
component
operation
metadata
```

Possible event types:

```text
RUN_STARTED
WORKLOAD_STARTED
REQUEST_OBSERVED
FAULT_INJECTED
OPERATION_FAILED
RETRY_OBSERVED
OPERATION_SUCCEEDED
WORKLOAD_COMPLETED
RUN_COMPLETED
```

Avoid hardcoding assumptions about LLM agents into the core event model.

Persist events as JSON Lines:

```text
.agentchaos/runs/<run-id>/events.jsonl
```

This event stream should become the foundation for future:

* replay
* visualization
* benchmarking
* observability
* distributed tracing

Treat it as an important API.

---

# 6. Result Analyzer

At the end of an experiment, calculate basic metrics:

```text
faults_injected
operations_observed
failed_operations
successful_operations
workload_exit_code
duration
```

Also derive an experiment outcome.

For MVP:

```text
PASSED
FAILED
RECOVERED
```

For example:

`RECOVERED` means a fault caused an operation to fail but the workload ultimately completed successfully.

Keep this logic explicit and testable.

Do not use an LLM to judge recovery.

---

# 7. Report

Create:

```text
.agentchaos/runs/<run-id>/
    scenario.yaml
    events.jsonl
    stdout.log
    stderr.log
    report.json
```

`report.json` should contain:

```json
{
  "run_id": "...",
  "scenario": "api-timeout-recovery",
  "result": "RECOVERED",
  "duration_ms": 4800,
  "faults_injected": 1,
  "failed_operations": 1,
  "operations_observed": 3,
  "workload_exit_code": 0
}
```

Add other useful fields if appropriate.

---

# Demo environment

Do NOT require an OpenAI or Anthropic API key for the MVP.

Create a deterministic local demo.

Build:

### Fake tool/API service

A small local HTTP server with endpoints such as:

```text
GET /customer/{id}
GET /orders/{id}
```

Return predictable JSON.

### Demo agent

Create a very small Python program representing an autonomous workload.

For example:

1. Call `/customer/123`.
2. If the request fails or times out, retry with exponential backoff.
3. Once successful, process the response.
4. Exit successfully.

This is deliberately simple.

The goal is to demonstrate that Agent Chaos can distinguish:

```text
dependency never fails
```

from:

```text
dependency fails + workload recovers
```

from:

```text
dependency fails + workload crashes
```

Create scenarios demonstrating at least:

```text
examples/scenarios/no_fault.yaml

examples/scenarios/api_latency_recovery.yaml

examples/scenarios/api_503_recovery.yaml
```

If useful, add a deliberately non-resilient demo workload to demonstrate a `FAILED` experiment.

---

# HTTP interception

Think carefully about the cleanest MVP architecture.

Prefer something like:

```text
demo agent
     |
     v
Agent Chaos Proxy
     |
     | fault injection
     v
fake API service
```

rather than requiring users to instrument individual function calls.

The eventual vision is:

```text
Agent
 |
 v
Chaos Layer
 |
 +------ Model API
 |
 +------ MCP
 |
 +------ HTTP API
 |
 +------ Database
```

For this MVP, only implement the HTTP path.

The proxy should:

1. receive a request
2. identify whether it matches a configured target
3. update occurrence counts
4. evaluate the trigger
5. inject the fault if applicable
6. otherwise forward the request
7. emit structured events

Keep proxy logic separate from scenario parsing and reporting.

---

# Testing

Write meaningful tests.

At minimum test:

### Configuration

* valid scenario parses correctly
* invalid fault definitions fail validation

### Trigger behavior

* occurrence trigger fires exactly when expected

### Fault injection

* latency fault introduces expected delay
* HTTP-error fault returns expected status

### Reporting

* events are persisted
* report metrics are calculated correctly

### Integration

Have at least one integration test that runs:

```text
demo workload
→ chaos proxy
→ fake API
```

injects a failure, verifies that the demo workload retries, and verifies that Agent Chaos reports `RECOVERED`.

Tests should not depend on external internet access or paid APIs.

---

# Repository structure

Choose an idiomatic structure approximately like:

```text
agent-chaos/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── agentchaos/
│       ├── cli.py
│       ├── config/
│       ├── runtime/
│       ├── faults/
│       ├── proxy/
│       ├── events/
│       ├── analysis/
│       └── reporting/
├── examples/
│   ├── demo_agent.py
│   ├── fake_api.py
│   └── scenarios/
├── tests/
└── docs/
```

Do not blindly follow this structure if a better organization becomes obvious.

---

# CLI

Initial commands:

```bash
agentchaos run <scenario>
```

Also provide:

```bash
agentchaos --help
agentchaos version
```

If useful:

```bash
agentchaos validate <scenario>
```

Do not create a large CLI surface yet.

---

# README

Create a strong README aimed at engineers.

The opening should immediately communicate:

# Agent Chaos

**Chaos engineering for autonomous AI agents.**

Then briefly explain the problem:

AI agents increasingly depend on unreliable external systems. Traditional tests usually verify happy paths, but production agents must survive rate limits, tool failures, database outages, malformed responses, latency, and partial infrastructure failures.

Agent Chaos intentionally breaks those dependencies so developers can measure whether their agents actually recover.

Include a simple architecture diagram in Mermaid:

```text
Agent → Agent Chaos → Dependency
```

and a quick-start example.

Do NOT make exaggerated claims such as:

```text
production-ready
enterprise-grade
industry-leading
```

This is an early open-source project.

---

# Engineering quality

Prioritize:

1. correctness
2. readability
3. modular architecture
4. testability
5. deterministic behavior
6. developer experience

Avoid:

* unnecessary abstractions
* giant classes
* premature distributed architecture
* excessive dependency usage
* framework lock-in
* fake complexity designed only to make the project look impressive

Use type hints throughout.

Add docstrings where they actually help.

Use structured logging where appropriate.

---

# Important product constraint

Do not turn this into an "LLM wrapper."

The interesting system is the infrastructure surrounding the agent.

The core concepts are:

```text
workload
fault
trigger
event
experiment
result
```

Keep these concepts independent from model vendors.

Later we can add first-class OpenAI, Anthropic, MCP, PostgreSQL, GitHub, Docker, and Kubernetes integrations.

---

# Future direction — DO NOT IMPLEMENT NOW

Design the code so that these ideas remain possible:

### More faults

```text
HTTP latency
HTTP 500
HTTP 429
malformed responses
connection resets

Postgres latency
Postgres connection failures

MCP server crashes
invalid MCP responses

filesystem read failures
filesystem corruption

model API errors
model latency
context-length failures
```

### More sophisticated experiments

```text
probabilistic faults
time windows
multiple simultaneous faults
fault schedules
chaos campaigns
```

### Agent-specific metrics

```text
retry count
replanning
fallback model usage
task success
cost increase
token increase
recovery latency
```

### Benchmarking

Eventually:

```bash
agentchaos benchmark benchmark.yaml
```

could compare agents/models/frameworks against a standardized failure suite.

Example result:

```text
                     Recovery   Cost Δ   Latency Δ

Agent A                94%       +18%      +22%
Agent B                71%       +44%      +61%
Agent C                87%       +23%      +30%
```

This could eventually become an **Agent Resilience Benchmark**.

Again: do not implement this yet.

---

# Your task

First inspect the repository and understand its current state.

If it is empty, initialize the project appropriately.

Then:

1. Briefly describe the architecture you intend to use.
2. Identify any design decisions that could be difficult to change later.
3. Implement the MVP vertical slice.
4. Add automated tests.
5. Add the local demo.
6. Write the README.
7. Run the tests and fix failures.
8. Run the example chaos experiment end-to-end.
9. Show me the resulting terminal output.
10. Summarize:

* what was implemented
* the architecture
* how to run it
* tests executed
* limitations
* the next 3 most logical improvements

Do not stop after scaffolding.

The final result should actually demonstrate:

```text
workload
   ↓
dependency call
   ↓
FAULT INJECTED
   ↓
operation failure
   ↓
retry/recovery
   ↓
successful workload
   ↓
RECOVERED report
```

That working vertical slice is the definition of done.
