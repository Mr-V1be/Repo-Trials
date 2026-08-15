# Agent recipes

One small POSIX `sh` wrapper per command-line coding agent. Each one reads the
task instruction the way the RepoTrials execution layer supplies it, checks that
the agent binary exists, and forwards the task in non-interactive mode.

They exist because `--agent-command` is **not** run through a shell: the string
is split with POSIX quoting rules and executed directly, so `$VAR`, `&&`, and
pipes do not work, and a literal `{` is read as a placeholder. A wrapper script
is the supported place to put that logic.

Read [`docs/agents.md`](../docs/agents.md) for the full contract.

## Use

```bash
repotrials run --agent-command "$PWD/recipes/claude-code.sh" \
  --name claude-code --model claude-sonnet-4-6 --unsafe-local
```

Use an **absolute path**: your shell's working directory is not the attempt's
working directory. Make sure the file is executable (`chmod +x recipes/*.sh`);
there is no shell to fall back on.

`--name` labels the run group and `--model` is recorded as metadata only —
neither is passed to the agent. Select the model inside the recipe, through
`RT_MODEL` where the recipe supports it, or in the agent's own configuration.

## Available recipes

| File | Agent | Notes |
| --- | --- | --- |
| `generic.sh` | any | Template. Copy it and change the last line. |
| `claude-code.sh` | Claude Code | Uses `--bare`; set `ANTHROPIC_API_KEY`. `RT_MAX_TURNS`. |
| `codex.sh` | OpenAI Codex CLI | `--sandbox workspace-write`; set `CODEX_API_KEY`. |
| `cursor-agent.sh` | Cursor CLI | Probes both `cursor-agent` and `agent`. |
| `aider.sh` | Aider | `RT_MODEL`. Disables auto-commit and auto-test. |
| `amp.sh` | Amp | |
| `opencode.sh` | opencode | `RT_MODEL` must be `provider/model`. |
| `goose.sh` | Goose | Sets `GOOSE_MODE=auto`. |
| `openhands.sh` | OpenHands CLI | Always-approve; model comes from its own config. |
| `qwen-code.sh` | Qwen Code | |
| `droid.sh` | Factory Droid | `RT_MODEL`, `RT_AUTO` (default `medium`). |
| `mini-swe-agent.sh` | mini-swe-agent | `RT_MODEL`. |
| `copilot-cli.sh` | GitHub Copilot CLI | `RT_TOOLS` narrows the tool allowlist. |
| `gemini-cli.sh` | Gemini CLI | `RT_MODEL`. `--yolo` unconfirmed for headless. |
| `crush.sh` | Crush | Auto-approve flag unconfirmed. |
| `continue-cn.sh` | Continue CLI | Flags not from a canonical reference. |
| `cline.sh` | Cline CLI | Flags not from a canonical reference. |
| `sweagent.sh` | SWE-agent | `RT_MODEL` required. Prefer `mini-swe-agent.sh`. |

Each script's header states which vendor documentation its flags came from and
whether they are confirmed. **None of these has been executed end to end
against a RepoTrials task by this project.** Vendor CLIs change flags often, so
run `<agent> --help` before publishing a number, and treat a run of all zeros as
a wiring bug until you have ruled one out.

## These scripts are not sandboxed

`repotrials run --unsafe-local` executes these wrappers, and therefore the
agent, as your operating-system user, with your files, your credentials, and
your network. Nothing here is a security boundary — the wrappers only make the
invocation correct and reproducible.

The agent will also send your source code to whichever model provider it is
configured to use. RepoTrials itself uploads nothing; the agent command is the
part that does.

Run them in a disposable clone inside a VM or container with no credentials you
care about, or use `repotrials export-harbor` and execute in a sandbox provider
that is an actual boundary. See [the threat model](../docs/threat-model.md).

## Contributing a recipe

Keep it under 30 lines, `#!/bin/sh` with `set -eu`, no bashisms. Check the
binary with `command -v` and exit 127 with a clear message. `cd` into
`$REPOTRIALS_WORKSPACE`, read `$REPOTRIALS_INSTRUCTION_PATH`, and finish with a
single `exec ... </dev/null` so the timeout kills the right process and the exit
code propagates. Verify with `sh -n recipes/<file>.sh`.

State in the header where the flags came from and whether you actually ran
them. An honestly-labelled unverified recipe is welcome; a confidently wrong one
is not.
