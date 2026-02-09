#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)

if [ -z "$REPO_ROOT" ]; then
  echo "✗ Not inside a git repository."
  exit 1
fi

HOOKS_DIR="$REPO_ROOT/.githooks"

if [ ! -d "$HOOKS_DIR" ]; then
  echo "✗ .githooks/ directory not found."
  exit 1
fi

chmod +x "$HOOKS_DIR"/*
git config core.hooksPath .githooks

echo "✓ Git hooks activated from .githooks/"
echo "  • pre-commit  — disallowed trailers + secrets check"
echo "  • commit-msg  — conventional commit format"
echo "  • pre-push    — disallowed trailers + secrets in push diff"
