#!/usr/bin/env bash
# Scan text on stdin for disallowed trailers and footers. Exit 1 if any is found.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if MATCHES=$(grep -inE "$(blocked_regex)"); then
  echo "$MATCHES"
  echo
  echo "Disallowed trailer or footer found in the lines above."
  echo "Squash merges are assembled by GitHub, so local hooks cannot catch them."
  echo "Reword the offending commits, force-push the branch, then fix the pull request text."
  exit 1
fi

echo "No disallowed trailers found."
