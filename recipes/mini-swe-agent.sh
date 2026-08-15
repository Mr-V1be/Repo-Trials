#!/bin/sh
# RepoTrials recipe: mini-swe-agent.
# Flags verified against the published mini usage documentation, August 2026.
#
#   RT_MODEL=<model-id> \
#   repotrials run --agent-command "$PWD/recipes/mini-swe-agent.sh" \
#     --name mini-swe-agent --model <model-id> --unsafe-local
#
# --exit-immediately is mandatory unattended; otherwise mini drops to a
# prompt after finishing and the attempt runs until the RepoTrials timeout.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v mini >/dev/null 2>&1; then
  echo "mini-swe-agent.sh: 'mini' is not on PATH (pipx install mini-swe-agent)" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

if [ -n "${RT_MODEL:-}" ]; then
  exec mini --task "$instruction" --yolo --exit-immediately \
    --model "$RT_MODEL" </dev/null
fi
exec mini --task "$instruction" --yolo --exit-immediately </dev/null
