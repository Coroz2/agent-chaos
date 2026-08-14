# Releasing Agent Chaos

Agent Chaos is published to PyPI as `agent-chaos-runner`. The import package and CLI command remain
`agentchaos`.

## One-time setup

1. In the GitHub repository, create an environment named `pypi`.
2. Add `Coroz2` as a required reviewer and leave self-review enabled.
3. In the PyPI account publishing settings, add a pending GitHub publisher with:
   - PyPI project name: `agent-chaos-runner`
   - GitHub owner: `Coroz2`
   - Repository: `agent-chaos`
   - Workflow: `release.yml`
   - Environment: `pypi`

A pending publisher does not reserve the package name. Complete this setup shortly before the first
release and verify that the spelling matches the workflow exactly.

## Prepare a release

1. Confirm `main` is synchronized with `origin/main` and green in GitHub Actions.
2. Create `release/vX.Y.Z` from `main`, using the intended release version in the branch name.
3. Confirm `pyproject.toml` contains that version.
4. Replace `YYYY-MM-DD` in the matching `CHANGELOG.md` heading with the release date.
5. Build and inspect the release archives:

   ```bash
   uv lock --check
   uv build --sdist --clear
   uv build --wheel dist/*.tar.gz
   uv run twine check --strict dist/*
   uv run python scripts/verify_dist.py dist
   ```

6. Run the complete local verification commands documented in the README.
7. Open a pull request with a `release: prepare vX.Y.Z` title, wait for `CI Gate` to pass, and
   squash merge it into `main`.
8. Update local `main` with `git pull --ff-only origin main` and confirm the merged release commit is
   checked out before tagging.

## Publish

Create and push an annotated tag from the release commit on `main` only after the one-time setup is
complete:

```bash
VERSION=$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
git tag -a "v$VERSION" -m "Agent Chaos v$VERSION"
git push origin "v$VERSION"
```

The release workflow validates the tag against package metadata, rebuilds and tests the package,
then waits for approval on the `pypi` environment. After approval it publishes the exact artifacts
to PyPI and creates the GitHub release.

## Verify

1. Confirm the release workflow completed successfully.
2. Confirm the wheel, source archive, and `SHA256SUMS` are attached to the GitHub release.
3. Install `agent-chaos-runner` from PyPI in a fresh environment.
4. Run `agentchaos --version` and confirm it prints the released version.
5. Confirm the PyPI project metadata links back to this repository.

Do not reuse or move a published version tag. PyPI release files are immutable; fixes require a new
version.
