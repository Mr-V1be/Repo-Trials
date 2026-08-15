#!/bin/sh
# RepoTrials recipe: SWE-agent.
# PARTIALLY VERIFIED: the subcommand and configuration flag names come from
# the published CLI page, but that page shows no complete populated example.
# Run `sweagent run --help` and confirm the composition before using it.
# The maintainers consider SWE-agent superseded by mini-swe-agent; prefer
# recipes/mini-swe-agent.sh for new work.
#
#   RT_MODEL=<model-id> \
#   repotrials run --agent-command "$PWD/recipes/sweagent.sh" \
#     --name swe-agent --model <model-id> --unsafe-local
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
: "${RT_MODEL:?set RT_MODEL to the model name SWE-agent should use}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v sweagent >/dev/null 2>&1; then
  echo "sweagent.sh: 'sweagent' is not on PATH" >&2
  exit 127
fi

exec sweagent run \
  --agent.model.name="$RT_MODEL" \
  --env.repo.path="$PWD" \
  --problem_statement.path="$REPOTRIALS_INSTRUCTION_PATH" </dev/null
