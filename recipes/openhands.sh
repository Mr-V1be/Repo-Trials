#!/bin/sh
# RepoTrials recipe: OpenHands CLI.
# Flags verified against the published headless documentation, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/openhands.sh" \
#     --name openhands --model <model-id> --unsafe-local
#
# Headless OpenHands always runs in always-approve mode; there is no
# permission dimension to configure and no safety rail. The model comes from
# OpenHands' own configuration, not from a flag.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v openhands >/dev/null 2>&1; then
  echo "openhands.sh: 'openhands' is not on PATH" >&2
  exit 127
fi

exec openhands --headless -f "$REPOTRIALS_INSTRUCTION_PATH" </dev/null
