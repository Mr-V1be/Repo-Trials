#!/bin/sh
# RepoTrials recipe: Qwen Code (Alibaba).
# Flags verified against the published headless documentation, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/qwen-code.sh" \
#     --name qwen-code --model <model-id> --unsafe-local
#
# Without --yolo or an --approval-mode the agent will not write files. Auth
# has been reported to behave differently in non-interactive mode; check the
# credentials before trusting a run of zeros.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v qwen >/dev/null 2>&1; then
  echo "qwen-code.sh: 'qwen' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec qwen --prompt "$instruction" --output-format text --yolo </dev/null
