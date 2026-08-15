<p align="center">
  <img src="docs/assets/hero.svg" width="100%" alt="RepoTrials — your repository is the benchmark">
</p>

<h1 align="center">RepoTrials</h1>

<p align="center">
  <strong>Turn bugs your team already fixed into a regression suite for every coding agent.</strong>
</p>

<p align="center">
  <a href="https://github.com/PozziTiv4ik/Repo-Trials/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/PozziTiv4ik/Repo-Trials/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/PozziTiv4ik/Repo-Trials/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/PozziTiv4ik/Repo-Trials/actions/workflows/codeql.yml/badge.svg"></a>
  <a href="https://github.com/PozziTiv4ik/Repo-Trials/releases/latest"><img alt="GitHub release" src="https://img.shields.io/github/v/release/PozziTiv4ik/Repo-Trials?sort=semver"></a>
  <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/License-Apache--2.0-blue.svg"></a>
</p>

RepoTrials turns a repository's own Git history into private, repeatable coding-agent evaluations. Public leaderboards tell you which agent wins in general; RepoTrials tells you which configuration can be trusted on your codebase.

```text
real fix commit → sealed historical task → equal agent trials → hidden verifier → evidence
```

<p align="center">
  <a href="#try-it-in-60-seconds"><strong>Try the demo</strong></a> ·
  <a href="#the-validation-contract">See how grading works</a> ·
  <a href="#quickstart">Build your benchmark</a> ·
  <a href="docs/threat-model.md">Read the threat model</a>
</p>

### Why teams use it

- **Behavioral grading:** real JUnit `FAIL_TO_PASS` and `PASS_TO_PASS`, never gold-diff similarity.
- **Leak-resistant workspaces:** agents receive a one-commit synthetic repository without future Git objects, hidden tests, or the human solution.
- **Auditable artifacts:** explicit settings, content-addressed vault objects, versioned JSON Schemas, and machine-readable run records.
- **Local by default:** private source and oracle data stay under `.repotrials/`; nothing is uploaded by RepoTrials.
- **Runner-friendly:** invoke any command-based agent locally or export a standalone task with a separate [Harbor](https://github.com/harbor-framework/harbor) verifier.

> **Project status:** v0.1.0 is the first public release. RepoTrials is under active development, and its command names and task schema may change before 1.0. Do not use current scores as a security or procurement certification.

## Try it in 60 seconds

The dependency-free demo creates a real two-commit repository, mines and validates one historical task, runs a no-op agent and a fixing agent, compares them, writes an HTML report, and exports the task for Harbor. It needs no model API key or third-party Python package.

```bash
git clone --depth 1 https://github.com/PozziTiv4ik/Repo-Trials.git
cd Repo-Trials
python -m pip install .
repotrials demo
```

```text
noop-agent   0/1 resolved
fix-agent    1/1 resolved
delta       +100 percentage points
```

That small example exercises the same public CLI used for a real repository: mining, BASE/RED/GOLD validation, sealed task material, agent trials, strict comparison, reporting, and Harbor export. Use `repotrials demo --output ./repotrials-demo` to keep the artifacts at a predictable path; `python scripts/demo.py` remains available in a source checkout.

## What v0.1 does

RepoTrials is a local-first Python tool that:

1. scans local Git history for changes that modify both implementation and Python tests;
2. reconstructs the repository immediately before a candidate fix;
3. separates the historical change into a hidden test patch and a reference, or "gold", patch;
4. validates the candidate with BASE, RED, and GOLD executions (RED is the no-op task state);
5. keeps verifier material outside the agent workspace in a local vault;
6. runs any agent that can be invoked as a command;
7. writes machine-readable JSON and human-readable HTML reports; and
8. exports accepted tasks in a Harbor-compatible layout.

The v0.1 pipeline uses path and patch-size filters before execution. A candidate is useful only when the old code fails the reconstructed tests and the historical fix passes them. Automatic validation is not a fairness decision; accepted task sets still need human review.

## What v0.1 does not claim

- It does not prove that every mined task is fair or fully specified. Human review remains part of the trusted workflow.
- It does not yet accept every behaviorally valid patch shape. v0.1 freezes the editable source-file set to paths touched by the historical human fix, so a solution that adds a new helper path is rejected even when its behavior would be correct.
- It does not guarantee freedom from model-training contamination, especially for public repositories.
- It does not support every language, test runner, monorepo, service dependency, or historical build environment.
- It does not reconstruct a complete Git checkout. v0.1 snapshots with `git archive`: `export-ignore` may omit tracked files, while submodule contents, hydrated Git LFS objects, and repository symlinks are unsupported. Review and reject candidates that depend on them.
- It is not a hardened sandbox for hostile code. Repository tests and agent commands are arbitrary programs; run them in an isolated environment.
- It does not upload repositories, tasks, or results to a hosted service by default.

See [the methodology](docs/methodology.md) for acceptance rules and [the threat model](docs/threat-model.md) before evaluating untrusted agents or repositories.

## Why another SWE evaluation tool?

RepoTrials complements existing projects rather than replacing them:

| Project | Primary purpose | How RepoTrials differs |
|---|---|---|
| [SWE-bench](https://github.com/SWE-bench/SWE-bench) | Evaluate agents on a maintained public corpus of real GitHub issues | RepoTrials generates a private corpus from one repository's own history |
| [SWE-rebench](https://github.com/SWE-rebench) | Continuously collect large, cross-repository executable datasets | RepoTrials optimizes for local ownership, reviewability, and repository-specific comparison |
| [SWE-smith](https://github.com/SWE-bench/SWE-smith) | Generate large-scale training tasks, including synthetic breakages | RepoTrials v0.1 mines historical human fixes and keeps the verifier private |
| [Harbor](https://github.com/harbor-framework/harbor) | Run agent evaluations and RL environments in standardized sandboxes | RepoTrials creates and validates repository-specific tasks and can export them to Harbor |
| [RepoAgentBench](https://github.com/HumphreySun98/repoagentbench) | Turn merged pull requests into local, replayable agent benchmarks with built-in agent adapters | RepoTrials mines local Git history without requiring a GitHub PR and emphasizes independent BASE/RED/GOLD validation plus strict cohort compatibility |
| [Superconductor](https://www.superconductor.com/benchmark) | Run a hosted benchmark from selected real PRs; multiple LLM judges compare quality, cost, and time | RepoTrials is a local benchmark-as-code workflow with test-transition grading and operator-owned artifacts |
| [Sigmabench](https://sigmabench.com/) | Provide a managed public leaderboard and own-codebase benchmarking service for accuracy, consistency, and speed | RepoTrials keeps task mining, validation, execution records, and comparison gates local and inspectable |

The important distinction is ownership: RepoTrials' engine can be public while a team's test split, hidden tests, and reference patches remain local.

RepoTrials' specific wedge is a private-by-default, local benchmark-as-code workflow: deterministic test-based BASE/RED/GOLD checks, frozen task contracts and digests, repeated validation, and a strict cohort gate before comparison. It does not try to replace hosted benchmark services or turn its results into a security certification.

## Requirements

- Git
- Python 3.11 or newer
- the dependencies needed to install and test the target repository
- an isolated machine, VM, or container when running code you do not fully trust

The portable v0.1 evaluation contract targets Linux/amd64. The CLI can be installed and exercised on Windows or macOS, but a local validation there is not evidence of equivalence to the exported Linux image; use matching Docker validation before comparison or Harbor export.

Harbor and third-party coding agents are optional and are not bundled.
The RepoTrials core has zero third-party runtime dependencies; contributor tools live in the `[dev]` extra.

## Install

RepoTrials is not on PyPI yet. Install the v0.1.0 wheel directly from GitHub Releases:

```bash
python -m pip install https://github.com/PozziTiv4ik/Repo-Trials/releases/download/v0.1.0/repotrials-0.1.0-py3-none-any.whl
repotrials --help
```

To inspect or contribute to the source, clone the repository and create a virtual environment:

```bash
git clone https://github.com/PozziTiv4ik/Repo-Trials.git
cd Repo-Trials
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
repotrials --help
```

For development, install `python -m pip install -e ".[dev]"` instead.

On Windows PowerShell, use this activation command in place of `source .venv/bin/activate`:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Quickstart

Run RepoTrials from the target repository. Start with a disposable clone: validation reconstructs historical states and executes their tests.

```bash
cd /path/to/your/repository

# Create local configuration and a private working area.
repotrials init

# Review repotrials.toml: especially setup, test command, and path globs.

# Check Git, repository state, and configured optional runners.
repotrials doctor

# Discover historical candidate fixes.
repotrials mine
repotrials candidates

# Execute BASE/RED/GOLD validation, create auto-tier tasks, and inspect them.
# RED is the historical no-op state: base code plus the hidden test patch.
repotrials validate --accept --unsafe-local
repotrials review

# After inspecting a task, promote it to the human-reviewed tier.
repotrials review --verify <task-id>
```

On a large history, use `repotrials mine --limit 100` or `--since <git-date>` for the first curation pass, then expand deliberately.

`--unsafe-local` acknowledges that validation executes historical setup and tests with your host permissions. Use `--backend docker` with a qualified image to avoid that host-local path; Docker still is not a hardened boundary for hostile code.

The plain `review` table is a triage view, not a full review interface. Use `repotrials --json review` plus the recorded candidate, validation, and source diff when applying the [human review rubric](docs/methodology.md#human-review-rubric). `--verify` records a local quality tier and timestamp; v0.1 does not collect a reviewer identity, rationale, or signature.

Keep `.repotrials/` out of the target repository's commits; it may contain proprietary source snapshots, hidden tests, gold patches, and raw logs.

Integrity-check the content-addressed objects at any time with `repotrials vault verify`.

See the [configuration reference](docs/configuration.md) before running validation on a nontrivial repository.

After accepting tasks, invoke a coding agent through a normal command and build reports:

```bash
repotrials run --agent-command '<command> {prompt}' --name <label> --unsafe-local
repotrials report <run-group>
repotrials compare <baseline-run-group> <candidate-run-group>
```

`{prompt}` is replaced with the complete frozen task instruction as one process argument. `{instruction}` expands to a file containing the same instruction, and `{workspace}` expands to the disposable agent workspace. No shell expansion is required. See the tested command shapes and isolation notes in [agent recipes](docs/agent-recipes.md).

`--unsafe-local` is a deliberate acknowledgement that the command is not sandboxed: it inherits the invoking user's host access and effective network. For an isolated downstream run, configure tasks for Harbor, revalidate them, and use `export-harbor` instead.

`run` prints a unique run-group identifier. Prefer that identifier for reports and comparisons. A durable group manifest is written before the first attempt and marked complete only after every expected result is stored; incomplete groups are rejected. A reused agent label can match several historical groups and is rejected as an ambiguous cohort. A bare `repotrials report` works only while the store contains exactly one run group.

Export accepted tasks for use with Harbor:

```bash
repotrials export-harbor --output .repotrials/exports/harbor
```

The v0.1 export targets stable Harbor v0.20.0 task schema 1.3. A bounded `[[verifier.collect]]` hook writes the complete Git diff to `/tmp/agent.patch`, anchored to a sealed baseline SHA that is unchanged even if the agent creates commits. Harbor transfers that single patch into a separate no-network verifier, which rejects any path outside the task's exact frozen submission allowlist; it does not transfer the raw agent workspace. See [task and result formats](docs/task-format.md#harbor-export) for the exact handoff and image assumptions.

Run `repotrials <command> --help` before automation. The CLI is still pre-release, and the help text is authoritative for flags and paths.

## The validation contract

For a base revision `B`, hidden test patch `T`, and historical gold patch `S`, RepoTrials checks:

```text
BASE   B       + original tests   -> pass
RED    B       + T                -> at least one relevant failure
GOLD   B + S   + T                -> pass
NOOP   B       + T                -> not resolved
```

During agent evaluation, the agent works on a clean export of `B` without the later Git history or vault. In a separate grading step, the verifier applies the agent patch and hidden tests, runs the frozen setup, and then executes evaluator-owned tests. A task is resolved only when all fail-to-pass tests pass and the protected regression tests remain passing.

The score is behavioral rather than a comparison with the gold diff. As a conservative v0.1 integrity rule, however, the evaluated patch may touch only the task's frozen `source_files`: the implementation paths changed by the historical fix. Local verification rejects an outside path; Harbor captures the complete bounded Git diff and its separate verifier rejects any outside or protected path. Either way, a solution that needs a new helper path cannot pass. Review the frozen path set along with the task, and see the [roadmap](docs/roadmap.md) for broader reviewed allowlists.

Execution establishes a reproducible signal; it does not by itself establish that the issue description and tests are aligned. Candidates intended for comparison should be reviewed before acceptance.

## Typical workflow

```text
Git history
    -> static candidate discovery
    -> historical reconstruction
    -> patch split
    -> BASE/RED/GOLD validation
    -> human review
    -> private task set
    -> agent runs
    -> JSON/HTML reports or Harbor export
```

More detail is available in:

- [Architecture](docs/architecture.md)
- [Agent recipes](docs/agent-recipes.md)
- [Mining and validation methodology](docs/methodology.md)
- [Task and result formats](docs/task-format.md)
- [Configuration reference](docs/configuration.md)
- [Threat model](docs/threat-model.md)
- [Roadmap](docs/roadmap.md)

## Interpreting results

Each attempt receives a binary resolved/not-resolved result. `report` then computes empirical task-level `pass@k`: a task is resolved when any of its `k` recorded attempts resolves it, and the primary rate is the macro fraction of resolved tasks. This is the observed result for the attempts actually run, not a combinatorial estimator from a larger sample. Partial fail-to-pass progress, regressions, runtime, and failure categories remain diagnostic signals.

The short message printed immediately by `run` is an attempt-level `resolved/trials` count. Use the run-group report for the canonical task-level aggregate.

Always record the complete evaluation configuration alongside a score:

- model and model revision;
- agent/scaffold version;
- prompt and tool configuration;
- token and wall-clock budget;
- the exact run-group identifier plus task-ID-to-content-digest and contract-digest mappings;
- RepoTrials version; and
- operating system and test environment.

The comparator requires one run group per side plus identical task sets, portable task-content and task-contract digests, attempt shapes, and recorded execution profiles. Model/provider settings outside that recorded profile still require operator review. See [methodology](docs/methodology.md#scoring-and-comparison).

## Privacy and safety

RepoTrials is local-first, but local does not automatically mean confidential or safe.

- An agent command inherits the permissions of the user that starts it.
- A hosted model provider may receive prompts or source code passed by the selected agent.
- Historical tests may execute network calls, destructive scripts, or resource-intensive workloads.
- The v0.1 vault separates verifier artifacts from the agent workspace; it is not a cryptographic secret manager.
- The generated Harbor agent image currently leaves `USER root` in effect inside the container unless the downstream provider overrides it; the container/provider must remain the security boundary.

Use a dedicated clone, remove credentials, disable unnecessary network access, impose resource limits, and read [SECURITY.md](SECURITY.md) before running third-party code.

## Contributing

Bug reports, task-quality cases, documentation improvements, and narrowly scoped adapters are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

Security issues should follow [SECURITY.md](SECURITY.md), not a public bug report.

Questions, implementation notes, and results from trying RepoTrials on a real codebase belong in [GitHub Discussions](https://github.com/PozziTiv4ik/Repo-Trials/discussions). Share the reproducible setup—not private tasks, hidden tests, or gold patches. If the project is useful, a GitHub star helps other agent builders find it.

## License

Licensed under the [Apache License 2.0](LICENSE).

## Citation

Citation metadata is provided in [CITATION.cff](CITATION.cff). Cite the release version and record the exact Git commit used for an evaluation.
