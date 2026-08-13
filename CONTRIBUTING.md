# Contributing to Agent Chaos

Thanks for helping improve Agent Chaos. Keep changes focused, tested, and easy to review. The
project uses a lightweight GitHub Flow: `main` is always releasable and all changes arrive through
short-lived pull requests.

## Development setup

Agent Chaos requires Python 3.12 or newer and uses [uv](https://docs.astral.sh/uv/) for dependency
and environment management.

```bash
git clone https://github.com/Coroz2/agent-chaos.git
cd agent-chaos
uv sync --extra dev --locked
```

## Branch workflow

Do not implement directly on `main`. Start from an up-to-date default branch:

```bash
git switch main
git pull --ff-only origin main
git switch -c feat/rate-limit-injection
```

Branch names use `<type>/<kebab-case-description>`. Choose the narrowest applicable type:

- `feat`: new user-visible behavior
- `fix`: bug fixes
- `docs`: documentation-only changes
- `test`: test-only changes
- `refactor`: behavior-preserving code restructuring
- `perf`: performance improvements
- `build`: packaging or dependency changes
- `ci`: continuous-integration changes
- `chore`: repository maintenance
- `release`: release preparation, named `release/vX.Y.Z`

Keep branches focused and short-lived. Rebase or update them from `main` before merging when GitHub
reports that they are out of date.

## Make changes

- Read `docs/PROJECT-SPEC.md` before changing runtime behavior or public contracts.
- Add or update tests for behavioral changes and regressions.
- Update examples and documentation when commands, configuration, or observable behavior changes.
- Add a concise entry under `Unreleased` in `CHANGELOG.md` for user-visible changes. Pure test,
  formatting, CI, and repository-governance changes normally do not need an entry.
- Do not commit generated run artifacts, virtual environments, caches, distributions, credentials,
  or secrets.

## Verify locally

Run the complete quality gate before opening a pull request:

```bash
uv lock --check
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For packaging, version, or release automation changes, also follow the build and archive checks in
`docs/RELEASING.md`.

## Open and merge the pull request

- Use a Conventional Commit-style title such as `feat: add rate-limit injection` or
  `fix(proxy): stop forwarding after disconnect`.
- Complete the pull request template and include the exact verification commands and results.
- Keep the pull request in draft while it is incomplete; mark it ready when the implementation,
  tests, and documentation are coherent.
- Resolve open review conversations and wait for the required `CI Gate` check to pass.
- Squash merge the pull request. The pull request title becomes the commit on `main`.
- Delete the source branch after merging.

Individual commits on a working branch may be incremental; the pull request title and final squash
commit carry the durable Conventional Commit message.

## Releases

Release preparation happens on `release/vX.Y.Z` and is merged through the same pull request and CI
process. After it reaches `main`, follow `docs/RELEASING.md` to create and push the annotated tag.
