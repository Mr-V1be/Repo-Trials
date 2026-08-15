# Agent integration

RepoTrials has one integration surface: a command. If your agent can be started
from a terminal and edit files in a directory, it can be benchmarked. There is
no plugin, no SDK, and no adapter class, and RepoTrials never contacts a model
provider itself — your agent does.

```bash
repotrials run --agent-command "<command>" --name <label> --unsafe-local
```

This page documents what that command actually receives, ready-to-paste
invocations for the common command-line agents, and how to turn two runs into a
defensible comparison. Working wrappers for each agent live in
[`recipes/`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/recipes/README.md).

## The execution contract

### Before your command starts

For each attempt RepoTrials creates a temporary directory and, inside it:

1. extracts the task's base tree — the repository as it stood immediately
   before the historical fix — from a `git archive` snapshot;
2. runs the frozen `test.setup` commands in that tree;
3. initializes a **synthetic Git repository**: `git init` on branch `trial`,
   one commit titled `RepoTrials sealed baseline`, no remote, no later history,
   no original commit IDs. Files produced by setup are inside that commit, so
   they never appear in your submission;
4. copies the resulting `.git` directory to an evaluator-owned path outside the
   workspace, so agent-controlled Git metadata cannot influence the diff;
5. writes the problem statement to `instruction.md`, also outside the workspace.

"Outside the workspace" means outside the directory your command starts in and
outside the synthetic repository. It is still the same temporary directory tree
on the same filesystem, owned by the same user — a defence against a
normally-behaving agent, not against a hostile one. See
[the threat model](threat-model.md).

If setup fails, the attempt is recorded as `setup_failed` and your command is
never started.

### What your command receives

| Input | What it is |
| --- | --- |
| Working directory | The task workspace. Your command starts already inside it. |
| `REPOTRIALS_WORKSPACE` | Absolute path to that same workspace. |
| `REPOTRIALS_INSTRUCTION` | The complete problem statement, as a single environment variable. |
| `REPOTRIALS_INSTRUCTION_PATH` | Absolute path to `instruction.md`. This file is **outside** the workspace, so reading the workspace alone will not find it. |
| `REPOTRIALS_TASK_ID` | The task identifier, useful for logging and per-task caching. |
| Rest of the environment | Inherited from the process that ran `repotrials`. Your `ANTHROPIC_API_KEY`, `CODEX_API_KEY`, `PATH`, and everything else are visible to the agent. |
| stdin | Inherited from the `repotrials` process — a terminal when you run it interactively. |
| stdout / stderr | Pipes, never a terminal. Both are captured in full and stored in the vault. Most agents detect this and select non-interactive behaviour on their own; pass the explicit flag anyway. |

Two placeholders are expanded in the command string itself:
`{workspace}` becomes the workspace path and `{instruction}` becomes the
instruction-file path.

The problem statement is derived from the historical issue, pull request, or
commit message, with commit SHAs, GitHub pull/commit/compare links, and lines
containing phrases such as "fixed by" stripped out. Nothing else is removed:
a commit-message-derived statement routinely still names a test file or
describes the change in prose. A statement that repeats a long run of the
gold patch verbatim is recorded as `prompt_risk: high` and excluded from
automatic acceptance, but that is the only content check. Read the prompt
before you trust a task — `repotrials --json review` prints it alongside
`prompt_source`, `prompt_risk`, and `prompt_findings`. See the
[human review rubric](methodology.md#human-review-rubric).

### There is no shell

The command string is split with POSIX shell quoting rules and handed to the
operating system as an argument vector. No shell interprets it. In practice:

- `&&`, `||`, `|`, `>`, and `;` are passed as literal arguments, not operators;
- `$VAR` is **not** expanded — the agent process receives the four `REPOTRIALS_*`
  variables in its environment, but `--agent-command "agent $REPOTRIALS_TASK_ID"`
  passes the literal string `$REPOTRIALS_TASK_ID`;
- a literal `{` or `}` is read as a placeholder, so an inline JSON argument such
  as `--schema {"a":1}` fails with `unknown command placeholder: a`.

Anything more complicated than one program and its flags belongs in a wrapper
script. That is what `recipes/` is for. `--agent-command "sh -c '...'"` also
works when you want a shell explicitly.

### What your command must produce

- **Edit files in place** under the working directory. RepoTrials reads the
  files on disk; it ignores your stdout, your commits, and any patch file you
  write. Committing is harmless — the sealed `.git` is restored before the diff
  is taken, so the submission is always *working tree vs sealed baseline*.
- **Stay inside the allowlist.** v0.1 freezes the editable set to the task's
  `source_files`: exactly the implementation paths the historical fix touched.
  A behaviorally correct patch that adds a new module is rejected. This is a
  documented v0.1 limitation, not a bug.
- **Exit 0.** A non-zero exit is recorded as `agent_exit` and the attempt cannot
  resolve, even if the edits on disk were correct. If your agent exits non-zero
  on ordinary success, fix that in the wrapper — but never blanket-`|| true`,
  because that also hides crashes and authentication failures as scores of zero.

### After your command exits

RepoTrials deletes `.repotrials-junit.xml` and `.coverage`, restores the sealed
`.git`, stages untracked files with `git add --intent-to-add --all`, and takes a
binary `git diff HEAD`. That patch is checked against the allowlist, the
protected paths (`tests/**`, `conftest.py`, `pyproject.toml`, `.github/**`, and
the rest), the file-count cap (`mining.max_files`, default 8), and a byte cap.

Only then does the hidden verifier run, in a **separate workspace your command
never saw**: clean base tree, plus your patch, plus the hidden test patch, then
frozen setup, then the frozen test command. JUnit XML is parsed and every test
is classified. An attempt resolves only when all of the following hold: no
failure kind, agent exit 0, verifier exit 0, integrity passed, every
`FAIL_TO_PASS` test passes, and every `PASS_TO_PASS` test still passes.

### Failure kinds

Every unresolved attempt is labelled, so a broken integration never looks like a
weak model.

| Failure kind | Meaning |
| --- | --- |
| `setup_failed` | Frozen setup failed before the agent started. |
| `agent_timeout` | The command exceeded `execution.timeout_seconds`. |
| `agent_exit` | The command returned non-zero. |
| `submission_capture` | The diff could not be taken — workspace deleted, symlink, non-UTF-8 path. |
| `integrity` | The patch left the allowlist, touched a protected path, or exceeded a cap. |
| `patch_apply` | The patch did not apply to a clean base tree in the verifier. |
| `verifier_setup`, `verifier_setup_mutation` | Setup failed or mutated state inside the verifier. |
| `junit_missing`, `junit_parse` | The test command produced no usable JUnit XML. |
| `expected_tests_missing` | A `FAIL_TO_PASS` or `PASS_TO_PASS` test never ran. |
| `tests_failed` | The honest outcome: tests ran and the patch did not resolve the task. |
| `infrastructure` | A RepoTrials-side error. |

Read the failure-kind column in the HTML report before reading the pass rate.
A column full of `agent_exit` means your wrapper is wrong, not that the agent
is bad.

### Timeout, attempts, and cost

- **Timeout** is `execution.timeout_seconds` (default `1200`) per attempt. On
  expiry RepoTrials kills the process it started; grandchildren spawned by your
  agent may survive, so avoid backgrounding work in a wrapper.
- **Attempts** is `execution.attempts` (default `3`). Each task is run that many
  times and counts as resolved if **any** attempt resolves it. The value is
  frozen into the task contract at validation time: `run --attempts N` is
  accepted only when `N` equals the frozen budget. To change it, edit
  `repotrials.toml` and revalidate.
- `--model` on `repotrials run` is **recorded metadata only**. It is stored on
  the run and shown in reports; it is never passed to your agent. Select the
  model inside the agent command or its own configuration.
- `--cost-usd` records that constant on *each* task attempt. It is not a
  run-group total.

## Ready-to-paste commands

Each row assumes you invoke the matching wrapper from
[`recipes/`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/recipes/README.md), which handles reading the instruction and
checking that the binary exists:

```bash
repotrials run --agent-command "$PWD/recipes/claude-code.sh" \
  --name claude-code --model claude-sonnet-4-6 --unsafe-local
```

Substitute the recipe name and the `--name`/`--model` labels for any other row.
The raw invocation is shown so you can inline it instead if you prefer.

**Status is about the vendor's documentation, not about RepoTrials.**
`docs-checked` means the flags were read from that vendor's current published
non-interactive reference in August 2026. `partial` means the source was vendor
material other than the canonical flag reference. Agents whose non-interactive
flags could not be confirmed from vendor material at all ship no recipe and no
row. **No invocation in this table has been
executed end to end against a RepoTrials task by this project** — the only
agents exercised in CI are the two synthetic ones in the bundled demo
(`src/repotrials/demo.py`, run in CI as `python scripts/demo.py`). Run
`<agent> --help` before you publish a number, and treat a run of all zeros as a
wiring bug until you have proven otherwise.

| Agent | Recipe | Non-interactive invocation | Status |
| --- | --- | --- | --- |
| Claude Code | `claude-code.sh` | `claude --bare -p "<task>" --allowedTools "Read,Edit,Bash" --permission-mode acceptEdits` | docs-checked |
| OpenAI Codex CLI | `codex.sh` | `codex exec --sandbox workspace-write --ephemeral --skip-git-repo-check "<task>"` | docs-checked |
| Cursor CLI | `cursor-agent.sh` | `cursor-agent -p --force --output-format text "<task>"` | docs-checked |
| Aider | `aider.sh` | `aider --message "<task>" --yes-always --no-auto-commits --no-auto-test` | docs-checked |
| Amp | `amp.sh` | `amp -x "<task>"` | docs-checked |
| opencode | `opencode.sh` | `opencode run --model <provider>/<model> --auto "<task>"` | docs-checked |
| Goose | `goose.sh` | `GOOSE_MODE=auto goose run --no-session -i <file>` | docs-checked |
| OpenHands CLI | `openhands.sh` | `openhands --headless -f <file>` | docs-checked |
| Qwen Code | `qwen-code.sh` | `qwen -p "<task>" --output-format text --yolo` | docs-checked |
| Factory Droid | `droid.sh` | `droid exec -f <file> --auto medium` | docs-checked |
| mini-swe-agent | `mini-swe-agent.sh` | `mini -t "<task>" -y --exit-immediately -m <model>` | docs-checked |
| GitHub Copilot CLI | `copilot-cli.sh` | `copilot -p "<task>" -s --allow-all-tools` | docs-checked, no documented JSON output or exit codes |
| Gemini CLI | `gemini-cli.sh` | `gemini -p "<task>" --yolo` | partial — `--yolo` is not on the headless page |
| Crush | `crush.sh` | `crush run --quiet "<task>"` | partial — auto-approve flag unconfirmed |
| Continue CLI | `continue-cn.sh` | `cn -p "<task>"` | partial |
| Cline CLI | `cline.sh` | `cline --yolo "<task>"` | partial |
| SWE-agent | `sweagent.sh` | `sweagent run --agent.model.name=<model> --env.repo.path=<dir> --problem_statement.path=<file>` | partial — superseded by mini-swe-agent |

Agents that do **not** fit this contract:

- **Devin** is a hosted REST API. It works in its own cloud VM, so RepoTrials
  cannot hand it the reconstructed pre-fix worktree or diff the result locally,
  and sending repository contents to a third party contradicts the reason most
  people use this tool. Not supported.
- **Warp Agent CLI** works locally via `oz agent run`, but its `run-cloud` mode
  sends repository contents off the machine. If you use it, use the local mode.
- **Roo Code** was discontinued in May 2026. Use Kilo Code.
- **Kilo Code**, **Codebuff**, and several other 2026-era CLI agents advertise
  headless modes that were not confirmed during this research. No recipe is
  shipped rather than shipping a guess.

Agent-specific traps worth repeating, because each one silently produces a score
of zero rather than an error:

- Cursor CLI without `--force` only *proposes* changes.
- Codex CLI defaults to a read-only sandbox.
- Factory Droid without `--auto` is read-only specification mode.
- Goose without `GOOSE_MODE=auto` waits for approval until the timeout.
- Aider commits by default; `--no-auto-commits` keeps the working tree clean.
  Never combine it with `--auto-test` pointed at the repository test command —
  that would run the hidden tests.
- Claude Code without `--bare` loads the *target repository's*
  `.claude/settings.json` hooks and `.mcp.json` servers, with no trust prompt in
  a print session. That is both a reproducibility hazard and a supply-chain one.

## Wrap your own agent

Copy [`recipes/generic.sh`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/recipes/generic.sh) and edit two lines: the
`AGENT=` assignment and the final `exec`. Left alone, `AGENT` defaults to
`true` (override it with `RT_AGENT`), so an unedited copy exits 0 having
changed nothing — an empty patch and a score of zero. A minimal wrapper is
only this much:

```sh
#!/bin/sh
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?run me through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

command -v my-agent >/dev/null 2>&1 || { echo "my-agent not on PATH" >&2; exit 127; }

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec my-agent --non-interactive --prompt "$instruction" </dev/null
```

Then point RepoTrials at the file. Use an absolute path — the working directory
of your shell is not the working directory of the attempt:

```bash
repotrials run --agent-command "$PWD/recipes/my-agent.sh" \
  --name my-agent --model my-model-v3 --unsafe-local
```

Four details that are easy to get wrong:

- `exec` keeps the agent as the direct child of RepoTrials, so the timeout kills
  the right process and the exit code propagates unchanged.
- `</dev/null` prevents an agent from inheriting your terminal and blocking on a
  prompt for the full attempt timeout.
- The instruction file is outside the workspace. Read it through
  `$REPOTRIALS_INSTRUCTION_PATH`, or use `$REPOTRIALS_INSTRUCTION` directly if
  your agent takes the prompt as a string.
- Make it executable (`chmod +x`). There is no shell to fall back on.

To sanity-check a wrapper without spending tokens, run it by hand:

```bash
mkdir -p /tmp/rt-probe && printf 'Fix the rounding bug.\n' > /tmp/rt-instruction.md
REPOTRIALS_WORKSPACE=/tmp/rt-probe \
REPOTRIALS_INSTRUCTION_PATH=/tmp/rt-instruction.md \
REPOTRIALS_INSTRUCTION="$(cat /tmp/rt-instruction.md)" \
REPOTRIALS_TASK_ID=task-probe \
  ./recipes/my-agent.sh; echo "exit=$?"
```

## Compare two configurations

The point of the tool. Two runs, one comparison, one number.

```bash
# Baseline: current production configuration.
repotrials run --agent-command "$PWD/recipes/claude-code.sh" \
  --name baseline --model claude-sonnet-4-6 --unsafe-local
# OK  run baseline-20260815-081633-d9b0ac: 4/12 trials resolved

# Candidate: same agent, different tool policy or model.
RT_MAX_TURNS=80 repotrials run --agent-command "$PWD/recipes/claude-code.sh" \
  --name candidate --model claude-sonnet-4-6 --unsafe-local
# OK  run candidate-20260815-082901-7be412: 7/12 trials resolved
```

Compare the two **run-group identifiers** printed above:

```bash
repotrials compare baseline-20260815-081633-d9b0ac candidate-20260815-082901-7be412
```

```text
baseline-20260815-081633-d9b0ac → candidate-20260815-082901-7be412
baseline  candidate  delta      paired tasks
--------  ---------  ---------  ------------
25.0%     50.0%      +25.0 pp   4
```

Those percentages are task-level `pass@k`, not the attempt-level `resolved/trials`
line that `run` prints. With `attempts = 3`, a task counts as resolved if any of
its three attempts resolved it. `repotrials --json compare ...` additionally
gives you `wins`, `losses`, `ties`, and the digests the comparison was checked
against.

Use it as a CI gate — `compare` exits 1 when the candidate drops by more than
the threshold:

```bash
repotrials compare "$BASELINE" "$CANDIDATE" --fail-on-regression 5pp \
  --output .repotrials/reports/gate.json
```

`compare` refuses to produce a number it cannot defend. It errors rather than
guessing when the two cohorts differ in task set, task-content digest,
task-contract digest, attempt shape, or execution profile. Practical
consequences:

- The selector must resolve to exactly **one** run group. Reusing the same
  `--name` twice makes that name ambiguous; always compare the printed
  run-group IDs.
- The execution profile hash covers the Python version, the platform string,
  and the RepoTrials version. Runs from two different machines, or from either
  side of an upgrade, will not compare. Run both cohorts back to back on the
  same host.
- Changing `repotrials.toml` and revalidating produces *different* tasks with
  different digests. Old runs will not silently blend into new ones.

## Caveats

**`--unsafe-local` is an acknowledgement, not a sandbox.** Your agent runs as
your operating-system user with your files, your credentials, and your network.
The workspace separation defends the oracle against a normally-behaving agent;
it protects nothing from a hostile one. Run this in a disposable VM or container
with no credentials you care about, or use `repotrials export-harbor` and
execute in a sandbox provider that is an actual boundary. See
[the threat model](threat-model.md).

**Your API keys reach third-party providers.** RepoTrials never contacts a model
provider, but the agents in this document do. The environment is inherited
wholesale, so every key in your shell is visible to the agent process, and the
agent will send your source code — the pre-fix base tree of your private
repository — to whichever provider it is configured to use. That is the one part
of "nothing is uploaded" the agent command breaks, and it breaks it by design.
On a hosted CI runner, `--unsafe-local` also hands the agent that runner's
credentials and network.

**Cost is real and multiplies.** A run is `tasks × attempts` full agent
sessions. Twenty tasks at the default three attempts is sixty sessions per
configuration, and a comparison needs two configurations. Start with
`--task <id>` and one or two tasks, measure the spend, then scale. Record the
result with `--cost-usd` so the report carries it.

**Agents are non-deterministic, which is why `attempts` defaults to 3.** The
same agent on the same task does not produce the same patch twice. A single
attempt per task measures one sample of a stochastic process. Higher `k` gives a
more stable ranking and costs proportionally more; `k = 1` is defensible only
when you are explicitly measuring single-shot behaviour and say so.

**The pass rate is empirical `pass@k`, not an estimator.** It is the observed
fraction of tasks resolved by at least one of the `k` attempts actually run. It
is not the unbiased `pass@k` estimator computed from a larger sample of `n`
attempts, and it does not extrapolate to a different `k`. Reports print a
deterministic bootstrap 95% confidence interval and the task count next to it
precisely so a four-task result looks like a four-task result.

**Fairness is a human judgement.** Validation proves a task is reproducible and
discriminating. It cannot prove the task is *fair* — a hidden test may require a
symbol the problem statement never names. Validated tasks land in tier `auto`;
promotion to `verified` is a separate human review. Do not rank agents on an
unreviewed corpus. See [the methodology](methodology.md).

**One more time on the allowlist.** v0.1 accepts edits only to the files the
historical human fix touched. If several of your tasks show `integrity`
failures across every agent, the tasks are probably too narrow — inspect the
frozen path set and reject the ones where no reasonable solution fits inside it.
