#!/usr/bin/env bash
# Builds the renderer release tarball that pins.env points at (RENDERER_URL, RENDERER_SHA256).
# usage: scripts/make-renderer-tarball.sh REIMS_CLONE M2V_CLONE OUT.tar.gz
# The two trees are `git archive` of the pinned commits, unchanged. The tar is sorted, with a fixed owner and mtime, and gzip -n
# stores no name or time, so the same pins give the same bytes and the printed sha256 is the one for pins.env.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
[ $# -eq 3 ] || { echo "usage: $0 REIMS_CLONE M2V_CLONE OUT.tar.gz" >&2; exit 2; }
# shellcheck disable=SC1091
. "$here/pins.env"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
git -C "$1" archive --prefix=reims-vgpu/ "$REIMS_REF" | tar -x -C "$work"
git -C "$2" archive --prefix=metal2vulkan/ "$M2V_REF" | tar -x -C "$work"
tar -C "$work" --sort=name --mtime='2026-01-01 00:00:00Z' --owner=0 --group=0 --numeric-owner --format=gnu -cf - metal2vulkan reims-vgpu \
  | gzip -n -9 > "$3"
sha256sum "$3"
