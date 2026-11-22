#!/usr/bin/env bash
# Prints the two lines that pin the arm64 source the runner downloads (DEFAULT_SOURCE_REF and DEFAULT_SOURCE_SHA256 in
# src/osx_proxmox_next/arm64.py). Run it once the beta is tagged: scripts/arm64-source-pin.sh v0.9.0
set -euo pipefail
ref=${1:?usage: arm64-source-pin.sh TAG_OR_COMMIT}
url=https://github.com/lucid-fabrics/osx-proxmox-next/archive/$ref.tar.gz
sum=$(curl -fsSL "$url" | sha256sum | cut -d' ' -f1)
printf 'DEFAULT_SOURCE_REF = "%s"\nDEFAULT_SOURCE_SHA256 = "%s"\n' "$ref" "$sum"
