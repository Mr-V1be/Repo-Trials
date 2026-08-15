# FAQ

Honest answers, including to the questions that do not flatter the project. Where v0.1 has a
known gap, it is named rather than softened.

## Privacy and safety

### Does my code leave my machine?

RepoTrials does not upload repositories, tasks, or results anywhere. Source snapshots, hidden
tests, gold patches, raw logs, and reports stay under `.repotrials/` in your working copy. The
core has zero third-party runtime dependencies and never contacts a model provider or a hosted
service.

Two exceptions, both explicit:

- `repotrials mine --github` is an opt-in networked enrichment step. It derives the repository
  slug from `remote.origin.url` and sends candidate commit SHAs to the GitHub REST API. Plain
  `repotrials mine` stays local.
- **The agent you benchmark is not RepoTrials.** If you point `--agent-command` at a CLI backed by
  a hosted model, that CLI will send your source code to its provider. That is the agent's
  behavior, not something RepoTrials mediates or can prevent.

### Is it safe to run?

No, not in the sense of "safe to point at untrusted code." RepoTrials executes arbitrary
historical setup scripts and arbitrary agent commands, and
[the threat model](threat-model.md#v01-security-statement) opens by saying v0.1 is not a hardened
sandbox.

The tool makes you acknowledge this in writing: local validation and local agent runs both refuse
to start without `--unsafe-local`. The vault and verifier separation reduce accidental disclosure
to a normally-behaving agent; they protect nothing from a malicious process running as the same
OS user. The Docker backend narrows the blast radius but is documented as not being a boundary
for hostile code, and the generated Harbor agent image currently leaves `USER root` in effect.

Recommended posture: a disposable clone inside a VM, no credentials on the box, no outbound
network beyond what the agent genuinely needs. For a genuinely untrusted agent, `export-harbor`
into a sandbox provider that actually is a boundary.

### Can I commit `.repotrials/`?

No. It may contain proprietary source snapshots, hidden tests, gold patches, and raw output.
`init` adds it to the repository-local Git exclude file. Only `report.json` and `report.html` are
safe to publish, and only after you have read them.

`repotrials.toml` holds benchmark policy rather than oracle material and can normally be
committed. Keep secrets out of it: setup and agent commands inherit the process environment.

## Scope and fit

### How is this different from SWE-bench? Why not just run that?

Run both; they answer different questions.

SWE-bench tells you which agent is stronger in general across twelve public Python projects. It
cannot tell you whether your agent configuration works on your internal service, with your test
harness, your fixtures, and your team's idioms. RepoTrials borrows SWE-bench's grading pattern
deliberately — fail-to-pass and pass-to-pass transitions — and changes only the corpus: yours,
private, and regenerable as your history grows.

The README carries a [comparison table](https://github.com/PozziTiv4ik/Repo-Trials#why-another-swe-evaluation-tool)
against SWE-bench, SWE-rebench, SWE-smith, Harbor, RepoAgentBench, Superconductor, and Sigmabench
that says where each one is the better tool.

### Does it work on non-Python repositories?

No. The supported v0.1 profile is a Git repository with Python tests and a runner that emits
JUnit XML through the `{junit}` placeholder. Pytest is the default; another Python runner works if
it honours the same contract.

Coarse path heuristics may recognise other file extensions during mining, but those are explicitly
not supported validation stacks. [The roadmap](roadmap.md) places Go, JavaScript/TypeScript, and
Java behind a proper adapter interface rather than a growing switch statement. If your repository
is not Python, v0.1 is not ready for you.

### How many tasks will my repository yield?

Quite possibly not many. A candidate must change implementation and Python tests in one bounded,
reconstructible commit, fail on the old code, pass on the historical fix, and survive repeated
execution. Plenty of histories produce a handful of tasks, not hundreds — especially teams that
land fixes and tests in separate commits, or squash large branches.

[The methodology](methodology.md#interpretation-limits) says outright that small repositories may
yield too few independent tasks for a stable ranking, and the report emits a deterministic
bootstrap 95% confidence interval and a task count next to the `pass@k` rate precisely so a thin
result looks thin.

Find out in an afternoon: `repotrials mine --limit 100` or `--since 12.months.ago`. The honest
failure mode here is "not enough signal," which the tool shows you rather than hides.

### Why did `mine` say `stored 0 candidate(s)` and exit successfully?

Because nothing failed — the filters simply rejected every commit they saw. A commit is dropped
when it touches more than `mining.max_files` paths, changes more than `mining.max_changed_lines`
lines, contains a binary or renamed file, matches no `test.source_globs` path, or matches no
`test.test_globs` path.

Known gap: v0.1 does not yet report the rejection breakdown, so you have to work through the
causes in order — globs first (they are the usual culprit on a non-standard layout), then the size
limits, then the scan bound. See [Quickstart step 5](quickstart.md#step-5-mine-candidates).

### Do I need Docker or Harbor?

Neither is required. `validation.backend = "local"` and local agent runs need only Git and
Python. Docker is an opt-in validation backend. Harbor is an optional export target for running
sealed tasks in a real sandbox downstream — `export-harbor` writes the task without Harbor being
installed.

## Grading

### What are BASE, RED, and GOLD?

Three executions that a candidate must pass before it becomes a task. For a base revision `B`, a
hidden test patch `T`, and the historical gold patch `S`:

```text
BASE   B       + original tests   -> pass
RED    B       + T                -> at least one relevant failure
GOLD   B + S   + T                -> pass
NOOP   B       + T                -> not resolved
```

RED is the state an agent starts from: the old code plus the hidden tests. If RED does not fail,
the test does not actually capture the bug. If GOLD does not pass, the reconstruction or the
environment is wrong. Each phase runs `validation.repeats` times, and any inconsistent outcome
rejects the candidate as flaky.

### What does "resolved" mean exactly?

```text
resolved =
    candidate patch applies
    AND every FAIL_TO_PASS test passes
    AND every protected PASS_TO_PASS test still passes
    AND the verifier completed without an infrastructure failure
```

It is binary and behavioral. The agent's diff is never compared to the gold patch. Partial
fail-to-pass progress, regressions, duration, exit category, and cost are recorded as diagnostics
but do not make an attempt resolved.

### What does `pass@k` mean here?

The empirical task-level rate: tasks with at least one resolved attempt, divided by evaluated
tasks, at the `k` you actually ran.

It is **not** the combinatorial estimator used when sampling `k` from a larger pool. Repeated
attempts are not treated as independent benchmark items; the reporter bootstraps task-level
pass/fail observations for the confidence interval. The short `resolved/trials` line printed by
`run` is an attempt-level count — use the run-group report for the canonical aggregate.

### Why can't the agent create a new file?

Because v0.1 freezes the submission allowlist to the task's `source_files`: the implementation
paths the historical fix changed. Local verification rejects an outside path, and the Harbor
verifier rejects any captured path outside the allowlist or protected set.

This is a deliberate conservative choice, and the cost is real: a behaviorally valid patch that
introduces a new helper module cannot pass. Reviewers are told to inspect the frozen path set and
reject tasks where no reasonable solution fits inside it. Reviewed broader source allowlists,
keeping the bounded patch-only verifier handoff, are the named next
[roadmap](roadmap.md) item.

### Aren't automatically mined tasks unfair? A hidden test can demand a name the prompt never states.

Yes, that exact failure mode is real, and it is the limitation the project is most explicit
about.

Automated BASE/RED/GOLD execution answers two questions — is this reproducible, is this
discriminating — and cannot answer the third, is this fair. So validated tasks land in tier
`auto`, not `verified`; promotion is a separate human command (`repotrials review --verify`), and
reports keep the two tiers distinct. The
[human review rubric](methodology.md#human-review-rubric) names your case as a rejection trigger:
collection and import failures where a hidden test demands a symbol the prompt never specifies
should be rejected, or the interface made explicit.

Known gap: v0.1 records a tier and a timestamp but no reviewer identity, rationale, or signature.

### My repository is public. Hasn't the model already seen the bug and the fix?

Probably, and that is listed under "what v0.1 does not claim" rather than buried.

RepoTrials removes the direct artifacts — later history, original commit IDs, remotes, the gold
patch, the hidden tests — from the agent workspace. That stops `git show`. It does nothing about
training data. The tool records an
[`exposure` label](methodology.md#contamination-and-leakage) per task (private and unpublished /
public but fixed after the model's release / public and fixed before it / unknown) so a
contaminated number is at least labelled as one.

There is no reliable general test proving a model never saw your source, and the project does not
pretend to have one. The strongest configuration is a private repository; the second strongest is
a public repository split chronologically after your model's cutoff, with cherry-picks and
backports clustered into a single split.

### Why did `compare` reject my two runs?

Because one of the cohort invariants did not hold. `compare` requires exactly one run group per
side, plus identical task IDs, task-content digests, task-contract digests, attempt shapes, and
recorded execution profiles.

The usual causes: you revalidated tasks between the two runs (which changes their digests), you
used different attempt budgets, or you passed an agent label that matches several historical run
groups. Pass the exact run-group identifier printed by `run`, not the `--name` label. An
incomplete run group — one whose manifest was never marked complete — is also rejected rather than
averaged.

### Why was `--attempts` rejected?

Validation freezes the attempt budget from `execution.attempts` into every task contract, and
`run --attempts` is accepted only when it equals the frozen value. Changing the budget means
changing `repotrials.toml` and revalidating, which produces different tasks. Set
`execution.attempts` before you validate.

## Operations

### How much does a run cost?

RepoTrials itself is free and local. Your bill comes from whatever the agent command calls.

The multiplier is what surprises people: cost scales as *tasks × attempts × the agent's per-task
cost*, and the default `execution.attempts` is 3. Twenty tasks at three attempts is sixty agent
invocations per configuration, and a comparison needs two configurations.

RepoTrials does not measure tokens or query provider pricing. `run --cost-usd <amount>` records
that constant amount on each individual task attempt so it appears in the report; it is an
operator-supplied annotation, not a run-group total and not a measurement. Keep cost separate from
correctness when you compare — a Pareto view is more honest than a composite score.

### Can I use it with Claude Code, Codex CLI, Aider, or my own scaffold?

If it can be started from a terminal and edit files in a directory, yes. `--agent-command` is the
whole integration surface: the command runs inside the task workspace and receives
`REPOTRIALS_WORKSPACE`, `REPOTRIALS_INSTRUCTION`, `REPOTRIALS_INSTRUCTION_PATH`, and
`REPOTRIALS_TASK_ID`, with `{workspace}` and `{instruction}` expanding to those paths. Whatever
the command leaves in the working tree is the submission.

Two things to get right for any agent: it must run non-interactively and auto-approve its own
edits, and it must not auto-commit or run the repository's tests on your behalf. See
[Agents](agents.md) for worked invocations.

### It is v0.1 with no PyPI release and no tag. Won't anything I build on it break?

Some of it will, and the status banner says so on the first screen.

What protects you is not stability promises but content addressing. Four versioned JSON Schemas
carry a `schema_version` that consumers are required to check and reject when unknown.
Task-content and task-contract digests are baked into every task ID, so changing a prompt, a
hidden test, a policy, or the environment produces a *different* task rather than silently
corrupting an old comparison. And the comparator refuses mismatched cohorts outright.

Practical advice: install from source, pin the commit, and record the digests alongside every
score. When the format moves, your old numbers will fail loudly instead of drifting quietly.

### Is RepoTrials on PyPI?

Not yet. Install from source; see [Quickstart](quickstart.md#step-1-install-the-cli). There is no
published release or tag at the time of writing, so cite the exact Git commit when you reference
a result.

### How do I report a bug or a bad task?

Use the [issue templates](https://github.com/PozziTiv4ik/Repo-Trials/issues/new/choose); there is
a dedicated task-quality template for tasks that validated but should not have. Security issues
follow [SECURITY.md](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/SECURITY.md), not a
public bug report.
