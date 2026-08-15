#!/bin/sh
# RepoTrials recipe: Cline CLI.
# PARTIALLY VERIFIED: flags come from the Cline CLI overview and package
# README rather than a canonical flag reference. Confirm with `cline --help`.
#
#   repotrials run --agent-command "$PWD/recipes/cline.sh" \
#     --name cline --model <model-id> --unsafe-local
#
# Cline switches to headless implicitly when stdout is redirected, which
# RepoTrials always does; --yolo makes that explicit rather than incidental.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v cline >/dev/null 2>&1; then
  echo "cline.sh: 'cline' is not on PATH (npm i -g cline)" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec cline --yolo "$instruction" </dev/null
