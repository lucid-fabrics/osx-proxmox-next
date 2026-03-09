#!/usr/bin/env bash
# One-shot installer for osx-proxmox-next TUI on Proxmox VE
# Usage: bash <(curl -sL https://raw.githubusercontent.com/lucid-fabrics/osx-proxmox-next/main/scripts/install.sh)
#   or:  bash install.sh [--branch NAME] [--uninstall]

set -euo pipefail

REPO="https://github.com/lucid-fabrics/osx-proxmox-next.git"
VENV_DIR="/opt/osx-proxmox-next"
BRANCH=""
UNINSTALL=false

# ── Parse args ──
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)  BRANCH="$2"; shift 2 ;;
    --uninstall) UNINSTALL=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Uninstall ──
if $UNINSTALL; then
  echo "Removing $VENV_DIR ..."
  rm -rf "$VENV_DIR"
  rm -f /usr/local/bin/osx-next /usr/local/bin/osx-next-cli
  echo "Done."
  exit 0
fi

# ── Check we're on Proxmox ──
if ! command -v qm &>/dev/null; then
  echo "ERROR: 'qm' not found — this script must run on a Proxmox VE node." >&2
  exit 1
fi

# ── Ensure Python 3.9+ ──
PYTHON=""
for candidate in python3 python3.12 python3.11 python3.10 python3.9; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major=${ver%%.*}
    minor=${ver#*.}
    if [[ $major -ge 3 && $minor -ge 9 ]]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "Python 3.9+ not found. Installing python3 ..."
  apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
  PYTHON="python3"
fi

echo "Using: $($PYTHON --version)"

# ── Create venv ──
if [[ -d "$VENV_DIR" ]]; then
  echo "Existing install found at $VENV_DIR — upgrading ..."
else
  echo "Creating venv at $VENV_DIR ..."
fi
"$PYTHON" -m venv "$VENV_DIR" 2>/dev/null || {
  echo "python3-venv missing, installing ..."
  apt-get update -qq && apt-get install -y -qq python3-venv >/dev/null
  "$PYTHON" -m venv "$VENV_DIR"
}

# ── Install package ──
PIP="$VENV_DIR/bin/pip"
"$PIP" install --quiet --upgrade pip

if [[ -n "$BRANCH" ]]; then
  echo "Installing from branch: $BRANCH ..."
  "$PIP" install --quiet --upgrade "git+${REPO}@${BRANCH}"
else
  echo "Installing from PyPI ..."
  "$PIP" install --quiet --upgrade osx-proxmox-next
fi

# ── Symlink binaries ──
ln -sf "$VENV_DIR/bin/osx-next" /usr/local/bin/osx-next
ln -sf "$VENV_DIR/bin/osx-next-cli" /usr/local/bin/osx-next-cli

# ── Verify ──
VERSION=$("$VENV_DIR/bin/osx-next-cli" --version 2>&1 || true)
echo ""
echo "Installed: $VERSION"
echo ""
echo "Commands:"
echo "  osx-next       # Launch TUI wizard"
echo "  osx-next-cli   # CLI mode (osx-next-cli --help)"
echo ""
echo "To uninstall:  bash $0 --uninstall"
