# Contributing to RepoTrials

Thank you for helping make repository-specific agent evaluation more trustworthy.

## Before opening a change

- Search existing [issues](https://github.com/PozziTiv4ik/Repo-Trials/issues) and pull requests before opening a change.
- For a bug, include a small disposable repository or fixture when possible.
- For a task-quality problem, explain which invariant failed: reconstruction, red/gold behavior, flakiness, prompt/test alignment, leakage, or scoring.
- Discuss broad schema changes and new language adapters with maintainers before investing in a large implementation.
- Report security issues through [SECURITY.md](SECURITY.md), not a public issue.

## Development setup

Use Python 3.11 or newer and create an isolated environment from a source checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On Windows PowerShell, activate the environment with
`.\.venv\Scripts\Activate.ps1` instead of `source .venv/bin/activate`.

Run the checks used in CI:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src/repotrials
python -m pytest --cov=repotrials --cov-report=term-missing
```

If the package metadata changes these commands, follow `pyproject.toml` and the current CI workflow.

## Design principles

Contributions should preserve these properties:

1. **Local-first.** Mining and evaluation should not require uploading a repository or oracle.
2. **Verifier separation.** Hidden tests and gold patches must not enter the agent workspace.
3. **Execution before inference.** Static heuristics may rank candidates; they must not replace red/gold validation.
4. **Fail closed.** Missing tests, unparsable output, skipped required tests, and ambiguous patch application must not receive credit.
5. **Auditability.** Important decisions should produce structured reasons and reproducible artifacts.
6. **Honest scope.** A stable red/gold result does not prove that a task is fair, secure, or contamination-free.
7. **Adapter isolation.** Language- and runner-specific behavior belongs behind a small, testable contract.

Read [docs/architecture.md](docs/architecture.md), [docs/methodology.md](docs/methodology.md), and [docs/task-format.md](docs/task-format.md) before changing the mining, verifier, or schema layers.

## Tests

Every behavior change should include tests. Prefer tiny synthetic Git repositories over network-dependent fixtures. Test at least the negative path for verifier and filesystem changes.

Particularly important cases include:

- missing or renamed commits;
- merge commits and unrelated history;
- patches that fail to apply;
- added, deleted, renamed, or parameterized tests;
- empty fail-to-pass sets;
- skipped or uncollected required tests;
- timeouts and malformed output;
- symlinks and paths outside the task root;
- agent commands that exit non-zero; and
- repeated validation with unstable outcomes.

Tests must not require access to private repositories, paid APIs, or persistent credentials.

## Documentation and compatibility

- Update user-facing docs in the same pull request as a CLI or schema change.
- Treat task and result formats as versioned interfaces, even before 1.0.
- Explain migration or compatibility impact in the pull request.
- Do not include benchmark scores that cannot be independently reproduced.
- Do not add badges, adoption claims, or publication claims without verifiable evidence.

## Pull requests

Keep pull requests focused and describe:

- the problem;
- the chosen design;
- tests performed;
- security or privacy effects;
- schema/CLI compatibility; and
- documentation changes.

By submitting a contribution, you agree that it may be distributed under the project's [Apache-2.0 license](LICENSE).

All contributors must follow the [Code of Conduct](CODE_OF_CONDUCT.md).
