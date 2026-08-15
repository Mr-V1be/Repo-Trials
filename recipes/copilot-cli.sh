#!/bin/sh
# RepoTrials recipe: GitHub Copilot CLI.
# Flags verified against the published programmatic reference, August 2026.
# Structured output and exit codes are not documented there; treat stdout as
# text and confirm with `copilot help` before parsing it.
#
#   repotrials run --agent-command "$PWD/recipes/copilot-cli.sh" \
#     --name copilot --model <model-id> --unsafe-local
#
# --allow-all-tools grants everything and Copilot inherits your access. Narrow
# it with RT_TOOLS, for example RT_TOOLS='write, shell(git:*)'.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v copilot >/dev/null 2>&1; then
  echo "copilot-cli.sh: 'copilot' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

if [ -n "${RT_TOOLS:-}" ]; then
  exec copilot -p "$instruction" -s --allow-tool="$RT_TOOLS" </dev/null
fi
exec copilot -p "$instruction" -s --allow-all-tools </dev/null
