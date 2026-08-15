# Continuous integration

RepoTrials ships a composite GitHub Action at the repository root (`action.yml`). It runs a command-based coding agent against a task set you have already validated, writes the JSON and HTML reports, optionally compares the result against a baseline run group, and fails the job when the candidate loses more ground than you allow.

`repotrials compare --fail-on-regression <pp>` exits 1 on a regression. The action is a thin, well-labelled wrapper around that exit code.

> The composite action lands in the next release. `@main` resolves it only once this change is merged; it is not in v0.1.0.

Read [the threat model](threat-model.md) before putting this in a pipeline. The [security section below](#security) states the specific consequences on a hosted runner.

## What the action does and does not do

The action performs three steps: `run`, `report`, and optionally `compare`.

It does **not** mine, validate, review, or accept tasks. Those are curation steps that need human judgement and are far slower than a normal CI step, so they belong outside the pipeline. The action fails early with a pointer to this page when it cannot find `repotrials.toml` and a `.repotrials/` state directory.

The action also does not check out your repository. Run `actions/checkout` yourself first, so you control the ref, the fetch depth, and the credential settings.

## Quickstart

```yaml
name: Agent evaluation

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  evaluate:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false

      # Put repotrials.toml and .repotrials/ on the runner first.
      # See "Getting the task set onto the runner" below.
      # The agent binary must also be on PATH; RepoTrials does not install it.

      - id: repotrials
        # Replace @main with a full commit SHA to pin this action.
        uses: PozziTiv4ik/Repo-Trials@main
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        with:
          agent-recipe: claude-code
          agent-name: claude-code
          unsafe-local: 'true'

      - run: echo "resolved ${{ steps.repotrials.outputs.resolved }} of ${{ steps.repotrials.outputs.trials }}"
```

The action writes a run summary to the job summary page: tasks resolved, resolve rate, the deterministic bootstrap 95% confidence interval, and the comparison table when a baseline was supplied. That is deliberate — the CLI's `report` command prints only a file path, which is invisible in a pipeline.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `agent-command` | required unless `agent-recipe` is set | Command that drives the agent. RepoTrials tokenises it with `shlex` and runs it **without a shell** in the sealed task workspace. `{workspace}` and `{instruction}` expand to absolute paths. |
| `agent-recipe` | `""` | Name of a wrapper bundled under [`recipes/`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/recipes/README.md), for example `claude-code`, `codex`, or `aider`. Supplying both this and `agent-command` is an error. |
| `agent-name` | required | Label recorded on every attempt. Not unique across runs; the action returns a unique run group derived from it. |
| `unsafe-local` | `false` | Must be the literal string `'true'`. The action refuses to run otherwise. |
| `python-version` | `3.13` | Interpreter used to install and run RepoTrials. 3.11 or newer. |
| `model` | `""` | Model identifier recorded with the run. Metadata only: it is **not** passed to the agent, and RepoTrials never contacts a model provider. Select the model inside your wrapper, or through `RT_MODEL` where the recipe supports it. |
| `attempts` | `""` | Attempts per task. Must equal the budget frozen into the task contracts at validation time. Leave empty to use the frozen budget. |
| `tasks` | `""` | Task IDs to restrict the run to, separated by newlines or commas. Empty means every accepted task. |
| `baseline-run-group` | `""` | Run group to compare against. Must already exist in the `.repotrials` store on this runner. |
| `fail-on-regression` | `""` | Regression budget in percentage points, `5` or `5pp`. Requires `baseline-run-group`. Empty reports the delta without gating. |
| `working-directory` | `.` | Directory inside the target repository. RepoTrials searches upward from here for `repotrials.toml`. |
| `report-output` | `repotrials-ci-report` | Directory receiving `report.json` and `report.html`. Relative paths resolve against `working-directory`. |

`agent-command` is passed to the CLI as a single argument and is never re-parsed by the shell. Because RepoTrials executes it with `shell=False`, pipes, `&&`, and variable expansion do not work; wrap the command in `sh -c '...'` when you genuinely need them, and understand that you are then running a shell with the runner's full privileges.

### Bundled recipes

`agent-recipe` avoids writing a wrapper at all. The action resolves the name against the `recipes/` directory of its own checkout, so the wrapper ships with the action and stays in step with it. The runner must still provide the agent binary on `PATH` and the provider credentials in the environment; RepoTrials installs neither.

Use `agent-command` instead whenever you need flags a recipe does not expose. [docs/agents.md](agents.md) documents the execution contract, the full recipe list, and what each invocation was verified against.

An unknown name fails in the action's preflight step and prints the available recipes, so a rename surfaces immediately rather than as a mid-run agent failure. A missing binary fails inside the attempt with exit 127, which the report records as an agent failure rather than an unsolved task — install the agent in an earlier step and smoke-test it there.

Two cohorts must actually differ in something the agent sees. The `model` input is recorded metadata and is not passed to the agent, so two steps that differ only in `model` run the same configuration twice and produce a meaningless delta. Change `RT_MODEL`, another documented recipe variable, or the command itself.

## Outputs

| Output | Meaning |
|---|---|
| `run-group` | Unique identifier for this run. Use it as the `baseline-run-group` of a later comparison. |
| `resolved` / `trials` | Attempt-level counts, matching the CLI's `resolved/trials` message. |
| `report-html` / `report-json` | Absolute paths to the generated reports. |
| `delta-pp` | Candidate pass@k minus baseline pass@k, in percentage points. Empty when no comparison ran. |
| `regressed` | `'true'` when the threshold was crossed, otherwise `'false'`. |

`resolved`/`trials` are attempt-level. The canonical task-level pass@k is in `report.json` and in the job summary. A run with 3 attempts on 4 tasks reports `trials: 12`, which is not a task count.

## Getting the task set onto the runner

The action needs `repotrials.toml` and a `.repotrials/` state directory containing accepted tasks. There are three workable arrangements, in decreasing order of how well they suit private code.

### Persistent runner (recommended for private code)

Keep a working copy with a validated task set on a self-hosted or ephemeral cloud runner and point `working-directory` at it. The oracle never enters GitHub-managed storage, and mining and validation happen once, out of band.

```yaml
jobs:
  evaluate:
    runs-on: [self-hosted, repotrials]
    steps:
      - uses: PozziTiv4ik/Repo-Trials@main
        with:
          working-directory: /srv/repotrials/my-service
          agent-command: 'my-agent --prompt-file {instruction}'
          agent-name: my-agent
          unsafe-local: 'true'
```

### Rebuild the task set in the job

Task identity is content-addressed: the task ID incorporates a task-content digest and a task-contract digest. An unchanged history plus an unchanged `repotrials.toml` therefore reproduces the same task IDs from a fresh `mine` and `validate`, so results stay comparable across rebuilds. Change a glob, a test command, or an attempt budget and you get *different* task IDs — which is the point, because the old numbers then fail to pair instead of quietly drifting.

```yaml
      - run: |
          repotrials mine --limit 200
          repotrials validate --accept --unsafe-local
```

This is honest but slow: validation executes BASE, RED, and GOLD for every candidate, repeated `validation.repeats` times. It also skips human review, so every task stays in the `auto` tier. Budget it accordingly and pin `--limit` or `--since`.

### Cache the state directory

`actions/cache` can carry `.repotrials/` between runs. **This caches hidden tests, gold patches, source snapshots, and raw logs into GitHub-managed storage.** Caches are readable by other workflow runs in the same repository, including runs triggered by pull requests from forks against the base branch. Only do this for a repository whose contents are already public, or where you accept that exposure.

```yaml
      - uses: actions/cache@v4
        with:
          path: .repotrials
          key: repotrials-${{ hashFiles('repotrials.toml') }}-${{ github.sha }}
          restore-keys: repotrials-${{ hashFiles('repotrials.toml') }}-
```

## Comparing across runs

`compare` reads both cohorts from the same local store. A run group produced by a previous workflow run is only comparable if that store still exists on this runner — through a persistent runner or the cache above.

A committed `report.json` is a record, not a comparable cohort. It documents a past score; it cannot be passed as `baseline-run-group`.

The simplest correct pattern avoids the problem entirely: run both agent configurations in the same job, against the same store, and compare them immediately. No persistence, no cache, no drift in the task set between the two cohorts.

```yaml
      - id: baseline
        uses: PozziTiv4ik/Repo-Trials@main
        with:
          agent-command: 'my-agent --model cheap --prompt-file {instruction}'
          agent-name: baseline
          unsafe-local: 'true'

      - id: candidate
        uses: PozziTiv4ik/Repo-Trials@main
        with:
          agent-command: 'my-agent --model expensive --prompt-file {instruction}'
          agent-name: candidate
          baseline-run-group: ${{ steps.baseline.outputs.run-group }}
          fail-on-regression: '5pp'
          unsafe-local: 'true'
```

RepoTrials refuses to compare two cohorts unless their task sets, task-content digests, task-contract digests, attempt shapes, and execution profiles all match. An ambiguous or incomplete run group is an error rather than a silent average. When `compare` fails, the JSON error on stderr names the mismatch; the usual cause is that the task set changed between the two cohorts.

## Publishing the report

`report.json` and `report.html` are safe to publish: they contain scores, digests, failure categories, and timings, not the oracle. The `.repotrials/` directory is not safe to publish.

Upload the report directory by name. The action's default `report-output` deliberately sits outside `.repotrials/` so a broad glob cannot scoop up hidden tests, and the action emits a warning if you point it inside.

```yaml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: repotrials-report
          path: repotrials-ci-report/
          if-no-files-found: error
```

Never do this:

```yaml
      # WRONG: publishes hidden tests, gold patches, and source snapshots.
      - uses: actions/upload-artifact@v4
        with:
          path: .repotrials/
```

Anyone who can read the repository can download a workflow artifact. On a public repository that is everyone.

## Security

Local execution is not a sandbox, and running it on CI concentrates the consequences.

**The job executes your repository's own test suite and an arbitrary agent command as the runner user.** Historical `test.setup` commands and historical test code are executed as they existed at the mined commit. Mining a commit from before a dependency was pinned means running whatever that commit resolved to.

**The agent process inherits the job's entire environment.** RepoTrials adds `REPOTRIALS_*` variables to the agent's environment but does not filter it: every secret exported into the step, and every credential on the runner, is visible to the agent. Scope secrets to the step that needs them rather than the job or the workflow.

**The agent's workspace is sealed; the agent process is not.** The agent receives a `git archive` export of the base tree wrapped in a fresh one-commit synthetic repository, with no later history, no commit IDs, no remotes, no gold patch, and no hidden tests. That separation defends against a normally-behaving agent reading the answer. It does nothing to stop the same process from reading the rest of the filesystem, including your checkout. Set `persist-credentials: false` on `actions/checkout` so the workflow token is not left in `.git/config`.

**Never run this on `pull_request` from a fork.** That trigger would let an outside contributor supply the code being executed. Use `workflow_dispatch`, `schedule`, or `push` on a protected branch.

**Do not run `repotrials --json review` in a public job.** Verified: it prints hidden fail-to-pass and pass-to-pass test node IDs and vault object identifiers to stdout, where they land in the build log.

Other practices worth adopting:

- Set `permissions: contents: read` on the workflow, and lower it further if the job does not need the checkout token.
- Pin the action to a full commit SHA rather than `@main`.
- Prefer an ephemeral runner. For private code that legally cannot leave your infrastructure, prefer a self-hosted ephemeral runner with no long-lived credentials and no outbound network beyond the model provider.
- Set a `timeout-minutes` on the job. Several coding agents have known conditions where a headless invocation never returns.
- For a genuinely untrusted agent, do not use this action. Use `repotrials export-harbor` and run the sealed task in a sandbox provider that is an actual boundary.

## Model API keys

RepoTrials itself never contacts a model provider, so it needs no credentials. Your agent command usually does.

Store the key as a repository or environment secret and expose it on the single step that runs the agent:

```yaml
      - uses: PozziTiv4ik/Repo-Trials@main
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        with:
          agent-recipe: claude-code
          agent-name: claude-code
          unsafe-local: 'true'
```

Note that the credential reaches the agent through the environment, not through `agent-command`. There is no shell in front of the agent, so a `$VARIABLE` written into `agent-command` is passed through as the literal string `$VARIABLE`. Wrappers read the environment themselves; that is one of the reasons they exist.

A GitHub Environment with required reviewers is a reasonable gate on a job that spends money on model tokens. Costs scale with tasks times attempts: a 40-task set at 3 attempts is 120 agent invocations per run.

## Interpreting a CI result

The job summary reports empirical task-level pass@k over the attempts actually run, next to a deterministic bootstrap 95% confidence interval. On a small task set that interval is wide, and a green job does not mean the change was an improvement.

Set `fail-on-regression` to a budget that your task count can actually resolve. With five tasks, one task flipping is twenty percentage points; a `5pp` budget will fail on noise. Prefer more tasks over a tighter threshold.

Record the model revision, the agent version, and the task digests alongside any score you keep. `report.json` carries the task-content digests, the task-contract digests, and the execution-profile hash for exactly this purpose.

## Troubleshooting

| Symptom | Cause and remedy |
|---|---|
| `no repotrials.toml found at or above ...` | The task set is not on the runner. See [Getting the task set onto the runner](#getting-the-task-set-onto-the-runner). |
| `selected tasks are frozen to N attempt(s)` | The `attempts` input disagrees with the budget frozen at validation time. Leave `attempts` empty, or change `execution.attempts` in `repotrials.toml` and revalidate. |
| `comparison requires identical task contracts` | The task set changed between the two cohorts. Rebuild both cohorts against the same store. |
| `... must resolve to exactly one non-empty run_group` | An agent label matched several historical run groups. Compare and report by run group, not by label. |
| `no candidates selected; run repotrials mine first` | Mining stored nothing, or nothing was accepted. Curate the task set locally before wiring up CI. |
| Job hangs until the runner timeout | The agent command is waiting for input. Add the agent's non-interactive and auto-approve flags, and keep `timeout-minutes` set. |

## Template workflow

[`.github/workflows/example-agent-regression.yml`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/.github/workflows/example-agent-regression.yml) is a complete, copy-pasteable example. It is `workflow_dispatch`-only on purpose, so adding it to a repository cannot start an agent on a push.
