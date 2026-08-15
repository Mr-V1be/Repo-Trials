# Agent recipes

RepoTrials can evaluate any coding agent that can be invoked as a command and edits its current working directory. It does not require an agent-specific SDK.

## Command contract

`repotrials run --agent-command` parses the supplied command into an argument vector without invoking a shell. These placeholders are expanded after parsing:

| Placeholder | Value |
|---|---|
| `{prompt}` | The complete frozen task instruction, kept as one process argument |
| `{instruction}` | Absolute path to a UTF-8 Markdown file containing the instruction |
| `{workspace}` | Absolute path to the disposable agent workspace |

The command runs with the workspace as its current directory. The same values are available as `REPOTRIALS_INSTRUCTION`, `REPOTRIALS_INSTRUCTION_PATH`, and `REPOTRIALS_WORKSPACE`; `REPOTRIALS_TASK_ID` identifies the task.

The agent should edit files in the current directory and exit. It does not need to print a patch. RepoTrials restores its sealed synthetic Git metadata, captures the complete working-tree diff, applies the hidden tests in a separate grading step, and rejects changes outside the task's frozen source allowlist.

## Common CLI agents

These command shapes use each tool's non-interactive mode. Review the selected agent's own authentication, model, permission, and network settings before running private code.

### OpenAI Codex CLI

```bash
repotrials run \
  --agent-command 'codex exec --ephemeral --sandbox workspace-write {prompt}' \
  --name codex \
  --model '<exact model revision>' \
  --unsafe-local
```

### Claude Code

```bash
repotrials run \
  --agent-command 'claude -p {prompt}' \
  --name claude-code \
  --model '<exact model revision>' \
  --unsafe-local
```

Claude Code permission rules still apply in print mode. Configure the minimum tool permissions needed to read, edit, and test the disposable workspace.

### OpenCode

```bash
repotrials run \
  --agent-command 'opencode run {prompt}' \
  --name opencode \
  --model '<provider/model>' \
  --unsafe-local
```

### Aider

```bash
repotrials run \
  --agent-command 'aider --message {prompt} --yes-always' \
  --name aider \
  --model '<provider/model>' \
  --unsafe-local
```

Agent-created commits are allowed: RepoTrials grades the full diff from the sealed baseline rather than trusting the agent's current `HEAD`.

## Wrapper scripts

Use a wrapper when an agent reads prompts from standard input, needs a complex permission profile, or records token and cost telemetry elsewhere:

```bash
repotrials run \
  --agent-command 'python /absolute/path/to/my_agent_wrapper.py {instruction}' \
  --name my-agent \
  --model 'provider/model@revision' \
  --attempts 3 \
  --unsafe-local
```

Keep the wrapper outside the target repository. It receives the instruction-file path as its first argument and starts in the disposable workspace.

## Fair comparison checklist

- Pin the agent/scaffold version and model revision.
- Keep prompt, tool, permission, network, token, and wall-clock budgets equal.
- Use the same reviewed task IDs and attempt count on both sides.
- Record settings outside RepoTrials alongside the generated run-group manifest.
- Compare run-group IDs, not reused display names.
- Treat provider-side updates and nondeterminism as part of the experimental uncertainty.

`repotrials compare` enforces identical tasks, task and contract digests, attempt shapes, and recorded execution profiles. It cannot inspect unreported provider-side settings.

## Security boundary

`--unsafe-local` is intentionally explicit. The agent command inherits the invoking user's host permissions and effective environment, and a hosted provider may receive private source. A disposable Git workspace is not a process sandbox. Use a dedicated machine or established container/VM boundary, remove unrelated credentials, restrict network access, and read the [threat model](threat-model.md).
