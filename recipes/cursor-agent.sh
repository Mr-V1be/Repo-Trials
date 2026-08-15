#!/bin/sh
# RepoTrials recipe: Cursor CLI.
# Flags verified against the published headless documentation, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/cursor-agent.sh" \
#     --name cursor --model <model-id> --unsafe-local
#
# Without --force the CLI only proposes changes, so every trial would score
# zero. The binary has shipped as both `cursor-agent` and `agent`. There are
# open reports of -p hanging; keep execution.timeout_seconds realistic.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if command -v cursor-agent >/dev/null 2>&1; then
  bin=cursor-agent
elif command -v agent >/dev/null 2>&1; then
  bin=agent
else
  echo "cursor-agent.sh: neither 'cursor-agent' nor 'agent' is on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec "$bin" --print --force --output-format text "$instruction" </dev/null
