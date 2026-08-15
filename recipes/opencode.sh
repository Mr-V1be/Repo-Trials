#!/bin/sh
# RepoTrials recipe: opencode.
# Flags verified against the published CLI documentation, August 2026.
#
#   RT_MODEL=anthropic/claude-sonnet-4-20250514 \
#   repotrials run --agent-command "$PWD/recipes/opencode.sh" \
#     --name opencode --model <model-id> --unsafe-local
#
# Two traps: the model must be given as provider/model, and -p in this CLI
# means --password, not print. Non-interactive mode is the `run` subcommand.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v opencode >/dev/null 2>&1; then
  echo "opencode.sh: 'opencode' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

if [ -n "${RT_MODEL:-}" ]; then
  exec opencode run --model "$RT_MODEL" --auto --format default "$instruction" </dev/null
fi
exec opencode run --auto --format default "$instruction" </dev/null
