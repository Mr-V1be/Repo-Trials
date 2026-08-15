#!/bin/sh
# RepoTrials recipe: Amp (Sourcegraph).
# Flags verified against the published manual, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/amp.sh" \
#     --name amp --model <model-id> --unsafe-local
#
# Amp enables execute mode automatically when stdout is redirected, which
# RepoTrials always does. The flag is passed explicitly anyway.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v amp >/dev/null 2>&1; then
  echo "amp.sh: 'amp' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec amp --execute "$instruction" </dev/null
