#!/bin/sh
# RepoTrials recipe: OpenAI Codex CLI.
# Flags verified against the published non-interactive documentation, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/codex.sh" \
#     --name codex --model <model-id> --unsafe-local
#
# The default sandbox is read-only, so workspace-write is required or the
# agent cannot edit anything. Authenticate with CODEX_API_KEY.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex.sh: 'codex' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec codex exec "$instruction" \
  --sandbox workspace-write \
  --ephemeral \
  --skip-git-repo-check \
  --ignore-user-config \
  </dev/null
