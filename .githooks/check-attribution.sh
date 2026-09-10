#!/usr/bin/env bash
# Scan text on stdin for AI attribution. Exit 1 if any is found.
# Lives in .githooks/ because the pre-commit hook exempts this directory,
# which is the only place the patterns can be written out literally.
set -euo pipefail

AI_PATTERNS=(
  '🤖'
  'Generated with.*Claude'
  'Generated with.*Codex'
  'Co-Authored-By:[[:space:]]*Claude'
  'Co-Authored-By:[[:space:]]*Codex'
  'Claude-Session:'
  'Codex-Session:'
  'anthropic\.com'
  'noreply@anthropic'
  'openai\.com'
  'claude\.ai/code/session'
)

AI_REGEX=$(IFS='|'; echo "${AI_PATTERNS[*]}")

if MATCHES=$(grep -inE "$AI_REGEX"); then
  echo "$MATCHES"
  echo
  echo "AI attribution found in the lines above."
  echo "Squash merges are assembled by GitHub, so local hooks cannot catch them."
  echo "Reword the offending commits, force-push the branch, then fix the pull request text."
  exit 1
fi

echo "No AI attribution found."
