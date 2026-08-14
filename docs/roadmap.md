# Roadmap

This roadmap communicates direction, not delivery dates. Items move only when they have tests, documentation, and an owner.

## v0.1: prove the local loop

Current scope:

- local-first mining from Git history;
- Python repositories and Python tests;
- conservative implementation/test patch classification;
- BASE, RED, and GOLD validation, with RED serving as the historical no-op state;
- candidate inspection and human review;
- local vault and verifier separation;
- generic command-based agent execution;
- JSON and HTML reports;
- side-by-side result comparison; and
- Harbor-compatible export.

The v0.1 success criterion is not the number of mined tasks. It is whether an operator can trace every accepted task from historical change through validation and final score without exposing the oracle to the agent.

## Next: improve confidence and ergonomics

- Richer flaky/infrastructure classifications beyond repeated phase exit status.
- Per-candidate structured diagnostics for static mining rejections.
- Stronger test-level result parsing and missing-test detection.
- Patch-id and content-similarity deduplication for cherry-picks/backports.
- Chronological train/dev/test split support.
- Richer review rationales and task-quality audit views.
- Environment fingerprints and dependency-lock diagnostics.
- Safer subprocess environment allowlisting and output limits.
- Stable schemas with migration commands.
- More explicit generic-agent input/output protocol.
- Harbor export conformance fixtures.
- Reviewed broader source allowlists, while retaining the bounded patch-only verifier handoff, so valid solutions may add approved helper paths without weakening verifier integrity.

## Later: broaden repository coverage

- Go adapter using structured `go test` output.
- JavaScript/TypeScript adapters for Jest and Vitest.
- Java adapters for Maven/Gradle and JUnit XML.
- Historical container-image recipes and reusable environment caching.
- Service-dependent task profiles with explicit opt-in.
- Inline-test/hunk classification for ecosystems such as Rust.
- Changed-test selection for repositories whose full suite is too expensive.

Language support will be adapter-driven. A large heuristic switch statement is not the intended architecture.

## Later: stronger task quality

- Gold-hunk ablation and patch minimization diagnostics.
- Mutation/coverage-based oracle-strength signals where mature tools exist.
- Multiple independent review annotations and disagreement handling.
- Investigator-agent assistance that produces auditable evidence, not automatic truth labels.
- Detection of prompt/test interface mismatches and overly exact assertions.
- Private rotating holdouts and task retirement after disclosure.

Recent audits of public SWE benchmarks show that executable does not necessarily mean fair. Task-quality work therefore takes priority over maximizing raw candidate count.

## Later: evaluation operations

- Remote execution through external sandbox providers without changing the local task format.
- Signed task-set manifests and result attestations.
- Reproducible OCI environment references.
- Organization-level policy configuration and result retention controls.
- Optional local viewer for run and task-audit traces.
- CI regression mode for tracking an agent configuration over a private rotating set.

## Explicitly not planned for the core

- A proprietary hosted model gateway.
- A public dump of users' private task sets.
- A single opaque score combining correctness, cost, and speed.
- Claims that an automated filter proves task fairness or eliminates training contamination.
- A home-grown multi-tenant sandbox in place of established isolation systems.

## How to propose roadmap work

Open a [feature issue](https://github.com/PozziTiv4ik/Repo-Trials/issues/new?template=feature_request.yml) describing:

- the user problem;
- why the core or an adapter is the correct layer;
- the proposed trust boundary;
- fixtures and negative tests;
- format/CLI compatibility; and
- the smallest useful increment.

See [CONTRIBUTING.md](../CONTRIBUTING.md).
