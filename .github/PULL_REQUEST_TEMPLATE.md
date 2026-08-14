## Problem

<!-- What user-visible or maintainer-visible problem does this change address? -->

## Approach

<!-- Summarize the design and why this is the appropriate layer. -->

## Verification

<!-- List exact tests/checks run. Include negative-path coverage for verifier, filesystem, or subprocess changes. -->

## Security and privacy

<!-- Describe effects on the agent workspace, verifier, vault, paths, commands, network, logs, or secrets. Write "No material change" only after checking. -->

## Compatibility

<!-- Note CLI, task/result schema, persistence, or migration impact. -->

## Checklist

- [ ] The change is focused and linked to an issue when appropriate.
- [ ] Tests cover the new behavior and important failure paths.
- [ ] `python -m ruff check .` passes.
- [ ] `python -m ruff format --check .` passes.
- [ ] `python -m mypy src/repotrials` passes.
- [ ] `python -m pytest --cov=repotrials --cov-report=term-missing` passes, including the configured coverage gate.
- [ ] User-facing CLI/schema changes are documented.
- [ ] No private source, hidden tests, gold patches, credentials, or fabricated benchmark results are included.
- [ ] Security-sensitive changes follow `SECURITY.md` and `docs/threat-model.md`.
- [ ] I agree to follow the Code of Conduct and license my contribution under Apache-2.0.
