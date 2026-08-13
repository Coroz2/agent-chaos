# Agent Chaos Repository Instructions

## Project

Agent Chaos is a Python 3.12+ CLI and library for deterministic chaos testing of autonomous
agent workloads. Keep the core framework-independent, model-vendor-independent, local- and
CI-friendly, and safe for captured workload data.

## Required context

- Before changing runtime behavior, scenario schemas, CLI contracts, events, reports, or public
  result semantics, read `docs/PROJECT-SPEC.md`.
- Before creating a branch, preparing commits, or opening a pull request, read `CONTRIBUTING.md`.
- Before changing versions, package metadata, build artifacts, tags, or publishing automation,
  read `docs/RELEASING.md`.

## Working agreements

- Never implement directly on `main`. Use a short-lived branch named according to
  `CONTRIBUTING.md`, unless the user explicitly requests a different branch.
- Preserve unrelated user changes. Do not rewrite or discard work you did not create.
- Treat scenario, event, and report schemas; CLI exit codes; reason codes; and artifact contents as
  public contracts. Make compatibility changes deliberately and test them.
- Never persist request headers, request bodies, raw query values, credentials, or other secrets in
  events, reports, or logs.
- Keep source, tests, examples, README guidance, the project specification, and changelog aligned
  with behavior. Add an `Unreleased` changelog entry for user-visible changes.
- Do not add production dependencies or broaden platform support without calling out the impact.

## Branches and pull requests

- Branch names use `<type>/<kebab-case-description>` with a type allowed by `CONTRIBUTING.md`.
- Pull request titles use Conventional Commit form, such as `feat: add rate-limit injection`.
- Keep branches focused and short-lived. Merge through a squash pull request and delete the branch
  after merging.
- `main` must remain releasable. Release tags are annotated and created only from commits on
  `main`, following `docs/RELEASING.md`.

## Canonical commands

Set up the locked development environment:

```bash
uv sync --extra dev --locked
```

Run the full verification suite before handoff:

```bash
uv lock --check
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For packaging or release-related changes, also run:

```bash
uv build --sdist --clear
uv build --wheel dist/*.tar.gz
uv run twine check --strict dist/*
uv run python scripts/verify_dist.py dist
```
