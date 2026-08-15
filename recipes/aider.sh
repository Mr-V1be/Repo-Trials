#!/bin/sh
# RepoTrials recipe: Aider.
# Flags verified against the published options and scripting pages, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/aider.sh" \
#     --name aider --model <model-id> --unsafe-local
#
# --no-auto-commits matters: Aider commits by default, and RepoTrials diffs
# the working tree against its own sealed baseline. Never enable --auto-test
# with the repository test command; that would run the hidden tests.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v aider >/dev/null 2>&1; then
  echo "aider.sh: 'aider' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

if [ -n "${RT_MODEL:-}" ]; then
  exec aider --model "$RT_MODEL" --message "$instruction" \
    --yes-always --no-auto-commits --no-auto-test </dev/null
fi
exec aider --message "$instruction" \
  --yes-always --no-auto-commits --no-auto-test </dev/null
