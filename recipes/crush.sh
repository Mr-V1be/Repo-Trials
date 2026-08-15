#!/bin/sh
# RepoTrials recipe: Crush (Charmbracelet).
# PARTIALLY VERIFIED: `crush run` and --quiet come from the project README and
# its CLI reference, not a hosted flag page. Whether a blanket auto-approve
# flag exists in your release is unconfirmed; check `crush run --help`. If the
# agent stops for approval it will simply burn the attempt timeout.
#
#   repotrials run --agent-command "$PWD/recipes/crush.sh" \
#     --name crush --model <model-id> --unsafe-local
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v crush >/dev/null 2>&1; then
  echo "crush.sh: 'crush' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec crush run --quiet "$instruction" </dev/null
