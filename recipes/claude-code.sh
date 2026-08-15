#!/bin/sh
# RepoTrials recipe: Claude Code (Anthropic).
# Flags verified against the published headless documentation, August 2026.
#
#   repotrials run --agent-command "$PWD/recipes/claude-code.sh" \
#     --name claude-code --model <model-id> --unsafe-local
#
# --bare skips the target repository's .claude/settings.json hooks and
# .mcp.json servers, which a print session would otherwise load without a
# trust prompt. It also skips the keychain, so export ANTHROPIC_API_KEY.
set -eu

: "${REPOTRIALS_INSTRUCTION_PATH:?missing; start this through \`repotrials run --agent-command\`}"
cd "${REPOTRIALS_WORKSPACE:-.}"

if ! command -v claude >/dev/null 2>&1; then
  echo "claude-code.sh: 'claude' is not on PATH" >&2
  exit 127
fi

instruction=$(cat "$REPOTRIALS_INSTRUCTION_PATH")

exec claude --bare --print "$instruction" \
  --allowedTools "Read,Edit,Bash" \
  --permission-mode acceptEdits \
  --output-format text \
  --max-turns "${RT_MAX_TURNS:-40}" \
  </dev/null
