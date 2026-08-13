## Summary

<!-- What changed? Keep this concise and describe the resulting behavior. -->

## Motivation

<!-- Why is this change needed? Link an issue when one exists. -->

## Verification

<!-- List the exact commands or manual checks run and their results. -->

- [ ] `uv lock --check`
- [ ] `uv run pytest`
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy`

## Impact checklist

- [ ] Tests cover new behavior or the reason tests are unnecessary is explained above.
- [ ] Documentation and examples reflect observable behavior changes, or no update is needed.
- [ ] `CHANGELOG.md` includes user-visible changes under `Unreleased`, or no entry is needed.
- [ ] Public scenario, event, report, CLI, exit-code, reason-code, and artifact contracts were
      preserved or the compatibility impact is documented above.
- [ ] No secrets, credentials, generated run artifacts, caches, or distributions are included.
- [ ] Release and packaging checks were run, or this change does not affect releases or packaging.
