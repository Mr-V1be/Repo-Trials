# Mining and validation methodology

RepoTrials mines historical human fixes to estimate how coding agents perform on a particular repository. The method is inspired by the fail-to-pass/pass-to-pass evaluation pattern established by [SWE-bench](https://github.com/SWE-bench/SWE-bench), but v0.1 operates locally and produces a private, repository-specific task set.

## Three independent quality questions

A trustworthy task must answer three different questions:

1. **Reproducible:** do the same code and tests produce stable results in the recorded environment?
2. **Discriminating:** does the old code fail while the known historical fix passes?
3. **Fair:** does the visible prompt contain enough information, and do the hidden tests accept reasonable alternative implementations?

Automated red/gold execution addresses the first two. It cannot prove the third. RepoTrials therefore keeps automatic validation and human acceptance as separate states.

## Task model

For each candidate:

- `B` is the base tree before a historical fix;
- `T` is the test-only part of that historical change;
- `S` is the implementation/reference part of the change;
- `I` is the reviewer-approved task prompt; and
- `E` is the recorded test environment.

The agent receives `B`, `I`, and the ordinary pre-existing tests. It does not receive `T`, `S`, or later Git history.

## Phase 1: candidate discovery

v0.1 walks local Git history and selects bounded changes that modify both implementation and recognizable Python-test paths. Commit-keyword matches are recorded as review metadata; they are not currently a filter or ranking score.

Useful positive signals include:

- a single-parent change reachable from the selected branch;
- at least one implementation path and one test path;
- added or modified assertions/test cases;
- a commit subject that describes a correction (a review signal only); and
- a patch small enough to reconstruct and review.

Typical early rejections include:

- documentation-, formatting-, generated-, or vendored-only changes;
- dependency-only changes;
- binary or submodule modifications;
- ambiguous merges or history that cannot be reconstructed locally;
- test deletion without a replacement behavioral signal;
- no implementation change; and
- a patch whose classified test and implementation regions overlap in a way v0.1 cannot separate safely.

Heuristics reduce execution cost. They never make a candidate valid by themselves.

## Phase 2: reconstruction

The safest simple historical pair is:

```text
B = first parent of fixed revision
H = fixed revision
historical patch = diff(B, H)
```

Merge and multi-commit changes require extra care because a base-to-merge diff can include unrelated branch movement. v0.1 can reconstruct a merge against its first parent and records that choice, but it does not prove that the resulting diff is single-purpose. Review merge candidates manually.

The historical base archive contains no `.git`. The local generic-command runner initializes a new one-commit synthetic repository so agents can use ordinary Git tooling, without remotes or later historical objects. This prevents straightforward access to the later fix through `git show`, but it does not address knowledge already present in a model's training data.

v0.1 creates that snapshot with `git archive`; it does not promise full Git-tree or checkout fidelity. Historical `.gitattributes` rules such as `export-ignore` can omit tracked files. Submodule contents, hydrated Git LFS objects, and repository symlinks are not reconstructed or supported. Reviewers must reject a candidate when its build, tests, setup, or fix depends on any of those features.

## Phase 3: patch classification

The historical diff is divided into:

- `T`, the hidden test patch; and
- `S`, the gold implementation patch.

Python test paths are recognized through repository configuration and conventional names such as `tests/`, `test_*.py`, and `*_test.py`. Test fixtures may need to travel with `T`. A file containing both production code and inline tests is an edge case and should be rejected by v0.1 unless the split is demonstrably safe.

Renames, deleted tests, generated snapshots, lockfiles, test-runner configuration, and shared fixtures receive explicit diagnostics. A path regex is a candidate classifier, not proof that a hunk belongs on the test or implementation side.

## Supported v0.1 stack

RepoTrials itself requires Python 3.11 or newer and has no third-party runtime dependencies. The supported mining and validation profile is a Git repository with Python tests. The configured test command should return a meaningful process exit status and, for test-level scoring, write JUnit XML through the `{junit}` placeholder. Pytest is the default; another Python runner is usable when it provides the same contract.

Validation can execute locally with the explicit `--unsafe-local` acknowledgement or through the optional Docker CLI. Generic command-based agent execution is local-first, also requires `--unsafe-local`, and does not require Harbor. Harbor is an optional export/runner integration. Other language extensions may be recognized by coarse path heuristics, but they are not supported validation stacks in v0.1.

## Phase 4: execution validation

RepoTrials evaluates these states in fresh workspaces:

| State | Tree | Test overlay | Required outcome |
|---|---|---|---|
| BASE | `B` | none | original suite is usable |
| RED | `B` | `T` | one or more relevant tests fail |
| GOLD | `B + S` | `T` | required and regression tests pass |
| NOOP interpretation | same execution as RED | the empty historical submission remains unresolved |

RED is also the historical no-op check: there is no separate fourth execution phase in v0.1. It guards against empty or misidentified fail-to-pass sets. GOLD proves only that the historical solution passes the oracle; it is not the only acceptable patch.

Evaluation does not compare a submission with the gold diff. v0.1 nevertheless freezes the allowed submission paths to the task's `source_files`, derived from implementation paths touched by the human fix. This deliberately conservative integrity boundary prevents other paths from participating in the evaluated patch, including a behaviorally valid solution that introduces a new helper file. Local verification rejects such a path; Harbor captures the complete bounded Git diff and its separate verifier rejects the attempt if any captured path is outside the allowlist or protected. Reviewers must inspect the frozen set; a reviewed broader source allowlist is roadmap work. Harbor's patch-only handoff preserves allowed additions, modifications, deletions, and agent commits but does not broaden the allowlist.

When test-level outcomes are available, RepoTrials classifies transitions:

```text
RED fail/error -> GOLD pass = FAIL_TO_PASS
RED pass       -> GOLD pass = PASS_TO_PASS
RED fail/error -> GOLD fail = FAIL_TO_FAIL
RED pass       -> GOLD fail = PASS_TO_FAIL
```

An accepted v0.1 task requires at least one `FAIL_TO_PASS`. Gold-side regression, an empty required-test set, uncollected required tests, patch-application ambiguity, timeout, or unparseable execution rejects the candidate.

Collection/import failures deserve special scrutiny. A hidden test that imports a new symbol absent from the prompt may require an agent to guess an exact name rather than implement the described behavior. Such tasks should be rejected or have the interface made explicit during review.

## Stability and flakiness

Repeated clean execution is recommended before trusting a task set. A trusted run should compare normalized `(test_id, status)` sets rather than raw output order.

Useful classifications are:

- `stable`;
- `test_flaky`;
- `environment_flaky`;
- `parser_unstable`; and
- `timeout_unstable`.

A disagreement between repeated RED or GOLD outcomes quarantines the candidate. Retrying until a desired result appears is not validation.

[BugSwarm](https://github.com/BugSwarm/bugswarm) similarly repeats historical fail/pass reproduction to identify flaky artifacts. [SWE-rebench V2](https://arxiv.org/abs/2602.23866) reports retaining instances only when structured outcomes remain unchanged across three validation runs.

## Human review rubric

Before promoting an automatically valid task, review:

### Prompt clarity

- Is the failure and expected behavior understandable from the prompt and base tree?
- Does the prompt rely on a link, screenshot, comment, or context the agent will not receive?
- Was the description derived from a fix message that reveals the implementation?
- Are required public names, signatures, formats, and error behavior explicit when the tests demand them?

### Test alignment

- Do hidden tests exercise only the visible requirement?
- Do they enforce internal structure, exact wording, ordering, or naming not required by the prompt?
- Could a materially incomplete or hard-coded solution pass?
- Does the test patch cover unrelated changes from the same commit?

### Patch isolation

- Are unrelated refactors, formatting, docs, generated output, or dependency updates mixed into the gold patch?
- Does the fix span multiple independent bugs?
- Does the test patch require production helpers that were misclassified?
- Is every reasonable solution expressible within the frozen `source_files`, or would the task require a new helper or another implementation path?

### Environment fidelity

- Is the selected Python/tool version consistent with the historical repository?
- Are dependencies locked or otherwise reproducible?
- Do tests require network, credentials, services, locale, time, or platform behavior?
- Does the historical state depend on `export-ignore`d files, submodule contents, hydrated Git LFS objects, or repository symlinks that the v0.1 archive does not reconstruct?

Reviewers should reject uncertain tasks rather than repair them invisibly. Material edits produce a new task digest and require revalidation.

## Hidden evaluation

During an agent run:

1. export a clean `B` workspace and create a synthetic one-commit Git repository, without the source history, remotes, or vault access;
2. provide the approved prompt;
3. run the configured agent command under an operator-supplied isolation boundary;
4. capture the candidate patch;
5. construct a fresh verifier workspace;
6. apply the candidate patch and then the hidden test overlay;
7. run the frozen setup commands and then evaluator-owned tests; and
8. require all expected tests to be present and successful.

Agent changes to hidden tests or RepoTrials verifier files must not determine the result. A missing required test is a failure, not silent success.

## Scoring and comparison

The primary task score is binary:

```text
resolved =
    candidate patch applies
    AND every FAIL_TO_PASS test passes
    AND every protected PASS_TO_PASS test still passes
    AND the verifier completed without an infrastructure failure
```

The primary aggregate is empirical task-level `pass@k`:

```text
tasks with at least one resolved attempt / evaluated tasks
```

The reporter bootstraps task-level pass/fail observations, so repeated attempts do not become independent benchmark items. RepoTrials also reports `k` when every task has the same attempt count and retains the underlying trial count. Partial fail-to-pass progress, regressions, duration, exit category, and cost metadata are diagnostics; partial progress does not count as resolved.

Setup, infrastructure, agent-exit, timeout, integrity, and verifier failures remain failed attempts with their individual `failure_kind`. Under empirical `pass@k`, another successful attempt for the same task can still resolve that task; the report retains the failed attempt rather than discarding it.

For stochastic agents, retain all independent attempts and report the attempt budget. v0.1's `pass@k` is the observed any-attempt result at the executed `k`, not the combinatorial estimator used when selecting `k` from a larger sample. Do not select the best run post hoc without reporting the attempt budget.

Cost, tokens, and time should remain separate from correctness. A Pareto comparison is more transparent than an unexplained composite score.

The unit under comparison is a complete system, including:

- model revision;
- agent/scaffold revision;
- prompt and tool set;
- context and token/time budget;
- test visibility mode;
- RepoTrials revision, per-task contract digests, and any externally maintained task-set digest; and
- environment.

Changing one of these creates a different condition. The comparator enforces identical task IDs, task-content and task-contract digests, attempt shapes, and recorded execution profiles; provider-side settings not present in that profile remain the operator's responsibility.

## Contamination and leakage

RepoTrials reduces direct artifact leakage by omitting the source Git history, original commit identifiers, remotes, gold patches, and hidden tests from the agent workspace. The local runner does not enforce network isolation; an external sandbox must do that.

It cannot prove that a model has never seen a public repository or fix. Useful exposure labels are:

- private and unpublished;
- public but fixed after a model's release;
- public and fixed before a model's release; and
- unknown.

Release date is only a proxy for training exposure. Private task sets should remain unpublished, and public tasks should be split chronologically with cherry-picks/backports clustered into a single split.

## Interpretation limits

A RepoTrials score estimates performance on the accepted tasks from one repository under one execution contract. It does not directly establish:

- general software-engineering ability;
- production safety;
- security-review ability;
- performance on new architectural work;
- human-equivalent productivity; or
- contamination-free model capability.

Small repositories may yield too few independent tasks for a stable ranking. Report the task count and confidence interval, not only a percentage.

## Related primary work

- [SWE-bench](https://github.com/SWE-bench/SWE-bench): issue-resolution tasks and fail-to-pass/pass-to-pass grading.
- [SWE-rebench](https://arxiv.org/abs/2505.20411): automated historical task collection, environment reconstruction, and temporal evaluation.
- [SWE-rebench V2](https://arxiv.org/abs/2602.23866): multilingual setup synthesis and repeated execution validation.
- [SWE-smith](https://github.com/SWE-bench/SWE-smith): scalable test-driven synthetic task generation.
- [BugSwarm](https://github.com/BugSwarm/bugswarm): repeated reproduction of historical CI fail/pass pairs.
- [OpenAI's 2026 SWE-bench Verified audit](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/): residual narrow/wide tests and public-data contamination.
- [OpenAI's 2026 SWE-Bench Pro audit](https://openai.com/index/separating-signal-from-noise-coding-evaluations/): overly strict tests, underspecified prompts, low coverage, and misleading requirements.
