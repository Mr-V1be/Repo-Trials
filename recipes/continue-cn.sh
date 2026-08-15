#!/bin/sh
# RepoTrials recipe: Continue CLI (cn).
# UNVERIFIED: flags come from Continue's quickstart and guides pages rather
# than a fetched flag reference, and this CLI has iterated quickly. Run
# `cn --help` and confirm before publishing any number produced with it.
#
#   repotrials run --agent-command "$PWD/recipes/continue-cn.sh" \
#     --name continue --model <model-id> --unsafe-local
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v cn >/dev/null 2>&1; then
  echo "continue-cn.sh: 'cn' is not on PATH (npm i -g @continuedev/cli)" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec cn -p "$instruction" </dev/null
