#!/usr/bin/env bash
# Record the osx-next TUI wizard as an animated GIF.
#
# Prerequisites (install on the Proxmox host):
#   apt install asciinema
#   cargo install --git https://github.com/asciinema/agg  (or download binary from releases)
#
# Usage:
#   ./scripts/record-demo.sh              # outputs docs/static/img/demo.gif
#   ./scripts/record-demo.sh my-demo.gif  # custom output path

set -euo pipefail

OUTPUT="${1:-docs/static/img/demo.gif}"
CAST_FILE="/tmp/osx-next-demo.cast"

command -v asciinema >/dev/null 2>&1 || { echo "asciinema not found. Run: apt install asciinema"; exit 1; }
command -v agg       >/dev/null 2>&1 || { echo "agg not found. See: https://github.com/asciinema/agg/releases"; exit 1; }

echo "Recording TUI wizard... Press Ctrl+D when done."
echo "Tip: walk through all 6 steps, including the dry-run preview."
echo

COLUMNS=120 LINES=40 asciinema rec \
  --title "osx-proxmox-next — macOS VM wizard" \
  --cols 120 --rows 40 \
  "$CAST_FILE"

echo
echo "Converting to GIF..."
agg \
  --cols 120 --rows 40 \
  --font-size 14 \
  --theme monokai \
  --speed 1.5 \
  "$CAST_FILE" "$OUTPUT"

echo "Done: $OUTPUT"
echo
echo "Embed in README.md:"
echo "  ![osx-proxmox-next demo](${OUTPUT#docs/})"
