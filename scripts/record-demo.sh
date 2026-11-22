#!/usr/bin/env bash
# Record the README demo (docs/static/img/demo-tui.gif) from scripts/demo/tui.tape.
# The tape runs the real TUI against a fake host (scripts/demo/run_demo.py), so no Proxmox is needed.
#
# Prerequisites: vhs (https://github.com/charmbracelet/vhs) and the dev install (pip install -e '.[dev]').
# Usage: scripts/record-demo.sh

set -euo pipefail

cd "$(dirname "$0")/.."
command -v vhs >/dev/null 2>&1 || { echo "vhs not found. See: https://github.com/charmbracelet/vhs#installation"; exit 1; }
vhs scripts/demo/tui.tape
