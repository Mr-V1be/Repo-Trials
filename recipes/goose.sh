#!/bin/sh
# RepoTrials recipe: Goose (Block).
# Flags verified against the published headless tutorial, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/goose.sh" \
#     --name goose --model <model-id> --unsafe-local
#
# GOOSE_MODE=auto is what actually makes the run non-interactive; without it
# Goose stops for approval and the attempt burns its whole timeout.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v goose >/dev/null 2>&1; then
  echo "goose.sh: 'goose' is not on PATH" >&2
  exit 127
fi

GOOSE_MODE=auto
GOOSE_DISABLE_SESSION_NAMING=true
export GOOSE_MODE GOOSE_DISABLE_SESSION_NAMING

exec goose run --no-session --with-builtin developer \
  -i "$REPOTRIALS_INSTRUCTION_PATH" </dev/null
