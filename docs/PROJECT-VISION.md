# Agent Chaos Project Vision

**Status:** Stable project direction

Agent Chaos is a framework-independent resilience test system for autonomous-agent workloads. It
deliberately introduces controlled failures into the dependencies surrounding a workload and
measures whether that workload tolerates, recovers from, or fails because of those conditions.

This document defines the project's durable identity and boundaries. It does not promise release
dates or assign features to versions. Released behavior is defined by the versioned specifications
listed in the [documentation index](README.md).

## Mission

Help developers answer a concrete engineering question:

> When a dependency fails in a known way, does this agent workload recover in a way we can observe
> and reproduce?

Agent Chaos should make that question practical in local development and continuous integration,
without requiring a particular model provider, agent framework, or paid external service.

## Audience

Agent Chaos is intended for:

- developers building autonomous or agent-like workloads;
- maintainers of agent frameworks, tools, and integration libraries;
- reliability and CI engineers evaluating failure handling;
- researchers comparing resilience behavior through reproducible experiments.

## Core concepts

The project is organized around concepts that remain independent of model vendors:

- **Workload:** the opaque program or system being evaluated.
- **Dependency:** an external capability used by the workload, such as HTTP, MCP, a database, a
  filesystem, a browser, or a model API.
- **Fault:** a controlled failure applied to a dependency interaction.
- **Trigger:** the deterministic condition that selects when a fault occurs.
- **Event:** a versioned record of an observed experiment action.
- **Experiment:** one configured workload run under defined dependency and fault conditions.
- **Recovery:** observable evidence that a workload successfully continued after a failed
  operation.
- **Result:** an explicit, machine-readable experiment outcome derived from recorded evidence.

## Permanent principles

### Deterministic by default

The same scenario should produce the same fault selection under the same sequence of operations.
Nondeterministic capabilities belong only where enough seed, schedule, and replay metadata exists
to reproduce a run.

### Framework-independent core

The core treats workloads as black boxes whenever practical. Framework-specific integrations may
improve signals later, but they must remain optional and must not redefine the experiment model.

### Open local and CI core

The primary product remains open source, runnable locally, and suitable for ordinary CI systems.
Remote execution, shared storage, or hosted interfaces may become optional layers, but they must
not be required to run core experiments or read their results.

### Explicit recovery evidence

Results come from structured events and defined rules, not workload log interpretation or an LLM
judge. Optional semantic grading may supplement a future experiment, but it must remain distinct
from deterministic core recovery classification.

### Versioned public contracts

Scenario schemas, events, reports, reason codes, CLI exit codes, and artifact formats are public
contracts. Changes must be deliberate, documented, testable, and accompanied by a compatibility
decision.

### Safe artifact capture

Agent Chaos records only the data needed to explain an experiment. Raw credentials, request
headers, request bodies, raw query values, and other sensitive dependency traffic must not be
persisted merely for convenience.

### Focused architecture

Extension points should support proven next capabilities without creating speculative distributed
infrastructure or generic plugin systems before they are needed.

## Capability map

The long-term system may grow across these capability families. Their presence here describes the
product shape, not release order or commitment.

### Workload execution

- local subprocess workloads;
- containerized or remote workloads;
- optional framework or callable adapters that preserve the common workload contract.

### Dependency adapters

- HTTP services;
- MCP servers;
- databases, filesystems, browsers, source-control services, and model APIs;
- additional adapters only when they have a clear failure model and deterministic test coverage.

### Faults and triggers

- latency, errors, rate limits, malformed responses, disconnections, and dependency termination;
- occurrence, schedules, windows, and reproducible probabilistic selection;
- single-fault experiments and ordered multi-fault campaigns with attributable outcomes.

### Experiment orchestration

- dependency setup and readiness;
- workload lifecycle and cleanup;
- repeatable scenarios, suites, and campaigns;
- local, CI, and optional remote execution using the same public experiment contracts.

### Evidence and analysis

- versioned event streams and reports;
- recovery, retry, fallback, and recovery-latency signals;
- optional workload instrumentation for richer metrics without making it mandatory;
- safe replay and visualization derived from captured events.

### Comparisons and benchmarks

- baseline-versus-fault comparisons;
- repeatable resilience suites;
- aggregate results suitable for CI and research;
- benchmark definitions that make assumptions, workloads, faults, and scoring reproducible.

## Product boundaries

Agent Chaos is not:

- an agent framework or orchestration SDK;
- an OpenAI, Anthropic, or other model-provider wrapper;
- a general-purpose production observability platform;
- a mandatory hosted service or distributed control plane;
- a transparent system-wide interception product;
- a store for raw sensitive traffic;
- an LLM-based semantic judge at the core of recovery classification.

The project should not adopt Kubernetes, queues, hosted databases, or similar infrastructure unless
a validated capability requires them and the local core remains usable without them.

## Decision and change policy

Ordinary feature work must conform to this vision. A change to the mission, primary audience,
permanent principles, core concepts, delivery model, or product boundaries requires a focused
product proposal that:

1. identifies the existing statement being changed;
2. explains the user need and alternatives considered;
3. describes compatibility, safety, and open-core impact;
4. receives explicit maintainer approval before implementation depends on it.

Release scope and sequencing belong in an approved version specification or a separately approved
roadmap. They must not be introduced silently into this document by an unrelated feature change.
