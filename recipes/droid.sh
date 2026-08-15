#!/bin/sh
# RepoTrials recipe: Factory Droid.
# Flags verified against the published droid exec documentation, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/droid.sh" \
#     --name droid --model <model-id> --unsafe-local
#
# With no --auto flag droid exec is read-only specification mode and will not
# edit anything. Do not add -w/--worktree here: RepoTrials already gives each
# attempt its own workspace and diffs that directory.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v droid >/dev/null 2>&1; then
  echo "droid.sh: 'droid' is not on PATH" >&2
  exit 127
fi

if [ -n "${RT_MODEL:-}" ]; then
  exec droid exec -f "$REPOTRIALS_INSTRUCTION_PATH" \
    --auto "${RT_AUTO:-medium}" --model "$RT_MODEL" </dev/null
fi
exec droid exec -f "$REPOTRIALS_INSTRUCTION_PATH" \
  --auto "${RT_AUTO:-medium}" </dev/null
