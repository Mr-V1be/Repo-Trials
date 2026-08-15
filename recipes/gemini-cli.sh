#!/bin/sh
# RepoTrials recipe: Gemini CLI (Google).
# Headless mode and --output-format verified against the published headless
# page, August 2026. --yolo is documented elsewhere in the Gemini CLI docs but
# not on that page; confirm with `gemini --help` before trusting a score.
#
#   repotrials run --agent-command "$PWD/recipes/gemini-cli.sh" \
#     --name gemini --model <model-id> --unsafe-local
#
# Exit 53 means the turn limit was hit; RepoTrials records it as agent_exit.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v gemini >/dev/null 2>&1; then
  echo "gemini-cli.sh: 'gemini' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

if [ -n "${RT_MODEL:-}" ]; then
  exec gemini --prompt "$instruction" --model "$RT_MODEL" --yolo </dev/null
fi
exec gemini --prompt "$instruction" --yolo </dev/null
