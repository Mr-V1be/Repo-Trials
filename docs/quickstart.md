# Quickstart

Two paths. The first proves the whole loop works on a throwaway fixture in a few seconds and
needs no API key, no Docker, and no configuration. The second points RepoTrials at a repository
you actually care about.

!!! danger "Run this against a disposable clone"

    Validation reconstructs historical revisions and **executes their setup scripts and test
    suites** on your machine. Agent commands run with your user's permissions and network access.
    Use a scratch clone with no credentials, ideally inside a VM or container. See the
    [threat model](threat-model.md#v01-security-statement).

## 1. See it work in a few seconds

The bundled demo builds a real two-commit Git repository, mines it, runs BASE/RED/GOLD
validation, then runs a deliberately broken agent and a working agent against the same sealed
task. It ends on a real red-to-green delta and a self-contained HTML report.

=== "One line, no clone"

    Requires [uv](https://docs.astral.sh/uv/) and network access to the repository. Nothing lands
    in your system Python: uv builds a throwaway environment, runs the demo, and discards the
    environment.

    ```bash
    uvx --with pytest --from git+https://github.com/PozziTiv4ik/Repo-Trials repotrials demo
    ```

    `--with pytest` is there because the *generated fixture repository* runs pytest, not because
    RepoTrials does. RepoTrials itself has zero runtime dependencies.

=== "Clone and install"

    ```bash
    git clone https://github.com/PozziTiv4ik/Repo-Trials.git
    cd Repo-Trials
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install ".[dev]"
    repotrials demo
    ```

    The `[dev]` extra is used here only because the generated fixture repository runs pytest. For
    the CLI alone, `python -m pip install .` is enough.

=== "Windows PowerShell"

    ```powershell
    git clone https://github.com/PozziTiv4ik/Repo-Trials.git
    cd Repo-Trials
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install ".[dev]"
    repotrials demo
    ```

    The CLI runs on Windows, but a local validation there is not evidence of equivalence to the
    Linux evaluation contract. Use matching Docker validation before comparing or exporting.

`repotrials demo --output <empty-dir>` keeps the generated repository somewhere you choose;
`repotrials demo --json` emits the same result as a machine-readable payload.

### Expected output

Abbreviated; the demo prints every CLI call it makes, so it doubles as a smoke test of the public
interface.

```text
$ repotrials --root .../demo-repository init
OK  initialized .../demo-repository/repotrials.toml
INFO  Review repotrials.toml, then run `repotrials doctor`.
$ repotrials --root .../demo-repository doctor
RepoTrials doctor
OK  git: /usr/bin/git
OK  docker: /usr/bin/docker
OK  harbor: not found (optional; export does not require it)
OK  repository: .../demo-repository
OK  state: .../demo-repository/.repotrials
$ repotrials --root .../demo-repository mine --limit 20
OK  stored 1 candidate(s), including 1 new
$ repotrials --root .../demo-repository candidates
Candidates (1)
ID                              commit     source  tests  lines  title
------------------------------  ---------  ------  -----  -----  --------------------------------------------------
candidate-cccfb93cafebceb33d74  a1772c9b3  1       1      7      Fix floating point cart total with regression test
$ repotrials --root .../demo-repository validate --repeats 1 --accept --unsafe-local
OK  candidate-cccfb93cafebceb33d74: valid

noop-agent  0/1 trials resolved
fix-agent   1/1 trials resolved
delta       +100 percentage points

OK  report written to .../demo-repository/.repotrials/reports/demo/report.html
OK  exported 1 Harbor task(s) to .../demo-repository/.repotrials/exports/harbor

Demo complete: /tmp/repotrials-demo-xxxxxxxx
Open report:   /tmp/repotrials-demo-xxxxxxxx/demo-repository/.repotrials/reports/demo/report.html
               xdg-open /tmp/repotrials-demo-xxxxxxxx/demo-repository/.repotrials/reports/demo/report.html
```

Measured at 3.9 s wall clock on a warm Linux laptop with the environment already built. The first
`uvx` invocation adds download and build time.

Open the printed `report.html` in a browser. It is a single self-contained file: task and attempt
counts, the resolved count, empirical task-level `pass@k` with a bootstrap 95% confidence
interval, and a per-attempt table with fail-to-pass and pass-to-pass transitions, integrity
result, failure kind, duration, and cost.

### What just happened

1. `mine` scanned the fixture's history and found one commit that changed implementation and
   tests together.
2. `validate` rewound the repository to the parent of that commit, split the commit into a hidden
   test patch and a gold patch, and ran three executions: BASE (old code, old tests) must pass,
   RED (old code, hidden tests) must fail, GOLD (fixed code, hidden tests) must pass.
3. `run` handed each agent a one-commit synthetic repository containing only the pre-fix tree,
   with no later history, no gold patch, and no hidden tests.
4. A separate verifier applied each agent's diff plus the hidden tests and graded the JUnit
   transitions. The no-op agent resolved nothing; the fixing agent resolved the task.

## 2. Run it on your own repository

Supported v0.1 profile: a Git repository with Python tests, and a test command that writes JUnit
XML through the `{junit}` placeholder. Pytest is the default.

### Step 1 — Install the CLI

RepoTrials is not on PyPI yet, so install from source.

=== "Linux / macOS"

    ```bash
    git clone https://github.com/PozziTiv4ik/Repo-Trials.git
    cd Repo-Trials
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install .
    repotrials --version
    ```

    ```text
    RepoTrials 0.1.0
    ```

=== "Windows PowerShell"

    ```powershell
    git clone https://github.com/PozziTiv4ik/Repo-Trials.git
    cd Repo-Trials
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install .
    repotrials --version
    ```

    ```text
    RepoTrials 0.1.0
    ```

### Step 2 — Initialize inside a disposable clone

```bash
cd /path/to/scratch-clone-of-your-repo
repotrials init
```

```text
OK  initialized /path/to/scratch-clone-of-your-repo/repotrials.toml
INFO  Review repotrials.toml, then run `repotrials doctor`.
```

`init` also adds `.repotrials/` to the repository-local Git exclude file. Keep it that way: that
directory holds source snapshots, hidden tests, gold patches, and raw logs.

### Step 3 — Review `repotrials.toml`

This is the step people skip, and it is the step that decides whether mining finds anything and
whether validation can execute what it finds. At minimum check four things:

| Setting | Why it matters |
|---|---|
| `test.command` | Must write parseable JUnit XML at `{junit}`. Exit status alone is not enough. |
| `test.setup` | Frozen commands run before every phase. Prefer pinned, already-installed dependencies. |
| `test.source_globs` / `test.test_globs` | Defaults assume `src/**/*.py` and `tests/**/*.py`. A different layout mines zero candidates. |
| `execution.attempts` | Validation **freezes** this budget into every task. `run --attempts` is accepted only when it equals the frozen value, so set it now. |

!!! warning "Run the configured test command by hand before you mine"

    The defaults are `setup = []` and `command = "python -m pytest -q --junitxml={junit}"`. They
    assume the suite is importable from a clean checkout with nothing installed. A `src/` layout
    is the common case where that is false: `python -m pytest` puts the repository root on
    `sys.path`, not `src/`, so collection ends in `ModuleNotFoundError` and every phase exits 2.
    Mining still stores the candidate; `validate` then reports
    `base_failed, gold_failed, no_fail_to_pass` without naming the cause.

    ```bash
    cd /path/to/scratch-clone-of-your-repo
    python -m pytest -q     # must collect and pass here before RepoTrials can reproduce anything
    ```

    For a `src/` layout, either tell pytest where the package lives:

    ```toml
    [test]
    command = "python -m pytest -q -o pythonpath=src --junitxml={junit}"
    ```

    or install the project into the evaluation environment:

    ```toml
    [test]
    setup = ["python -m pip install -e ."]
    ```

    The `pythonpath` form needs no network, which matters for `--backend docker`: validation
    containers run with networking disabled.

Start small while you are calibrating:

```toml
[validation]
repeats = 1        # raise to 3+ before trusting a task set

[execution]
attempts = 1       # raise before comparing stochastic agents
```

The full list is in the [configuration reference](configuration.md).

### Step 4 — Check prerequisites

```bash
repotrials doctor
```

```text
RepoTrials doctor
OK  git: /usr/bin/git
OK  docker: /usr/bin/docker
OK  harbor: not found (optional; export does not require it)
OK  repository: /path/to/scratch-clone-of-your-repo
OK  state: /path/to/scratch-clone-of-your-repo/.repotrials
```

`doctor` exits non-zero if a required check fails.

### Step 5 — Mine candidates

Bound the first pass. A full history scan on a large repository is slow and you do not yet know
whether your globs are right.

```bash
repotrials mine --limit 100
repotrials candidates
```

```text
OK  stored 1 candidate(s), including 1 new
Candidates (1)
ID                              commit     source  tests  lines  title
------------------------------  ---------  ------  -----  -----  --------------------------------------------------
candidate-9a64148473302a0bc248  50055c2a1  1       1      7      Fix floating point cart total with regression test
```

`--since 12.months.ago` is the other useful bound. Plain `mine` is entirely local; only
`mine --github` makes a network call.

!!! info "`stored 0 candidate(s)` is the most common first result"

    Mining is deliberately conservative. A commit is rejected when it touches more than
    `mining.max_files` paths, changes more than `mining.max_changed_lines` lines, includes a
    binary or renamed file, matches no `test.source_globs` path, or matches no `test.test_globs`
    path. In v0.1 the command reports success with a zero count and does not yet break the total
    down by reason.

    Work through it in this order: confirm your source and test globs match the real directory
    layout, then raise `mining.max_files` and `mining.max_changed_lines`, then widen `--limit` or
    `--since`. Repositories whose bug fixes rarely ship with a test in the same commit will
    genuinely yield very few tasks — see the [FAQ](faq.md).

### Step 6 — Validate and accept

```bash
repotrials validate --accept --unsafe-local
```

```text
OK  candidate-9a64148473302a0bc248: valid
```

`--unsafe-local` is a required acknowledgement that historical setup and tests execute with your
host permissions. It is not a sandbox switch. For containerized validation use
`--backend docker` with a pinned image; Docker narrows the blast radius but is still not a
boundary for hostile code.

Invalid candidates print their rejection reasons instead, and the command exits non-zero.

!!! warning "`base_failed` on the first attempt means the base tree could not run its own tests"

    ```text
    WARN  candidate-297aacc5a9c85534b185: base_failed, gold_failed, no_fail_to_pass
    ```

    Reasons are not ranked, and later ones are usually consequences of the first. `base_failed`
    means `test.command` did not pass on the untouched pre-fix tree, before any patch was applied
    — so nothing downstream of it can succeed. On a `src/` layout it is almost always collection
    failing with `ModuleNotFoundError`, because nothing put the package on the path. Add whatever
    your contributors run to `test.setup` and revalidate.

    Known gap: v0.1 does not persist the phase logs, so `validate` cannot show you the traceback.
    `repotrials --json review` records the per-phase exit codes (`baseline_exit_codes`,
    `red_exit_codes`, `gold_exit_codes`), which tell you whether the suite ran and failed or never
    ran at all. To see the output itself, run the phase by hand:

    ```bash
    repotrials --json candidates          # note the candidate's parent_sha
    git worktree add --detach /tmp/rt-base <parent_sha>
    cd /tmp/rt-base && python -m pytest -q --junitxml=/tmp/rt.xml
    ```

### Step 7 — Review before you trust the numbers

Execution proves a task is reproducible and discriminating. It cannot prove the task is *fair* —
that a reasonable agent could infer, from the prompt alone, the interface the hidden test
demands. So accepted tasks land in tier `auto`, and promotion is a separate human decision.

```bash
repotrials review
```

```text
Tasks (1)
ID                       tier  candidate                       instruction
-----------------------  ----  ------------------------------  ----------------------------------------------------------------------------
rt_1d07d86f4524da53e827  auto  candidate-9a64148473302a0bc248  Fix floating point cart total with regression test\x0a\x0aFix floating point
```

`\x0a` is the table's own escaping of a newline, not corruption in the prompt: instructions are
multi-line, and the triage table flattens control characters so that untrusted repository text
cannot forge rows in your terminal or CI log. The column is a hard 70-character cut with no
ellipsis.

The plain table is triage, not a review interface. Use `repotrials --json review` together with
the recorded candidate, validation, and source diff, and apply the
[human review rubric](methodology.md#human-review-rubric). Then promote:

```bash
repotrials review --verify rt_1d07d86f4524da53e827
```

```text
OK  updated 1 task(s)
```

!!! note "Global flags come before the subcommand"

    `--root` and `--json` belong to the top-level parser: write `repotrials --json review`, not
    `repotrials review --json`.

### Step 8 — Run an agent

`--agent-command` is the entire integration surface. The command runs inside the task workspace
and receives `REPOTRIALS_WORKSPACE`, `REPOTRIALS_INSTRUCTION`, `REPOTRIALS_INSTRUCTION_PATH`, and
`REPOTRIALS_TASK_ID`; `{workspace}` and `{instruction}` expand to those paths. Whatever the
command leaves in the working tree is the submission.

```bash
repotrials run \
  --agent-command "my-agent --workspace {workspace} --prompt {instruction}" \
  --name my-agent-baseline \
  --unsafe-local
```

```text
OK  run my-agent-baseline-20260815-083343-5ce2a4: 1/3 trials resolved
```

Copy that run-group identifier. It is the stable handle for reports and comparisons; a bare agent
label can match several historical groups and is rejected as an ambiguous cohort.

Worked invocations for Claude Code, Codex CLI, Aider, and others are in [Agents](agents.md).

### Step 9 — Report and compare

```bash
repotrials report my-agent-baseline-20260815-083343-5ce2a4 --output .repotrials/reports/baseline
```

```text
OK  report written to .repotrials/reports/baseline/report.html
```

Run a second configuration under a different `--name`, then put the two cohorts side by side:

```bash
repotrials compare noop-agent-20260815-083343-5ce2a4 fix-agent-20260815-083343-00c22d
```

```text
noop-agent-20260815-083343-5ce2a4 → fix-agent-20260815-083343-00c22d
baseline  candidate  delta      paired tasks
--------  ---------  ---------  ------------
0.0%      100.0%     +100.0 pp  1
```

`compare` refuses two cohorts unless their task sets, task-content digests, task-contract
digests, attempt shapes, and execution profiles match. Add `--fail-on-regression 5pp` to make it
exit 1 when the candidate drops by more than five percentage points — that is the CI gate, and
[CI](ci.md) shows it in a workflow.

## Housekeeping

```bash
repotrials vault verify        # hash-check every stored oracle object
```

- Never commit or publish `.repotrials/`. Only `report.json` and `report.html` are safe to share.
- Record the model revision, agent revision, prompt and tool configuration, budgets, the
  run-group identifier, and the RepoTrials version alongside any score you quote.
- One or two tasks is not a benchmark. The report prints a task count and a bootstrap 95%
  confidence interval next to `pass@k` precisely so a thin result looks thin.

## Next steps

- [Agents](agents.md) — verified invocations for real command-line coding agents.
- [CI](ci.md) — gate a pull request on an agent regression.
- [Configuration](configuration.md) — every key, with defaults.
- [Methodology](methodology.md) — what the score means and where it stops meaning anything.
- [FAQ](faq.md) — contamination, cost, non-Python repositories, and the rest of the hard questions.
