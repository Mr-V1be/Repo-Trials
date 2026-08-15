#!/bin/sh
# RepoTrials recipe: template for any command-line coding agent.
#
# Copy this file, change the last line, and point RepoTrials at the copy:
#
#   repotrials run --agent-command "$PWD/recipes/my-agent.sh" \
#     --name my-agent --model my-model --unsafe-local
#
# RepoTrials runs this script with the task workspace as the working
# directory and no shell in between, so every variable below comes from the
# environment the execution layer sets. See docs/agents.md for the contract.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

AGENT=${RT_AGENT:-true}
if ! command -v "$AGENT" >/dev/null 2>&1; then
  echo "generic.sh: '$AGENT' is not on PATH" >&2
  exit 127
fi

# The full problem statement, identical to "$REPOTRIALS_INSTRUCTION".
instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

# Replace this with your agent's non-interactive invocation. It must edit
# files in place under the current directory and exit 0 when it is done.
exec "$AGENT" "$instruction" </dev/null
