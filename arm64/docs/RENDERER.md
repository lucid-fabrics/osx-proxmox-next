# Renderer sources

`snapintosh build` compiles two Rust trees into the QEMU binary. They come as one release tarball (`RENDERER_URL` in `pins.env`), checked against `RENDERER_SHA256` before anything is unpacked.

| Tree | Origin | Pinned commit | Base |
|------|--------|---------------|------|
| `reims-vgpu/` | github.com/steelbrain/reims-vgpu | `81e0125b5594639caff4a3fc7e38446e00cb499b` | `f54dcb4f` plus two commits of ours |
| `metal2vulkan/` | github.com/steelbrain/metal2vulkan | `e0a4042424a36d96822b7eb7ab54f6a3af5ad1e2` | `d4ac1fd7` plus six commits of ours |

Both are licensed GPL-2.0-or-later OR LGPL-3.0-or-later; the license files are inside each tree.

Why a tarball: the upstream commits these trees were built on are no longer served by the upstream repositories, so a build from upstream cannot be reproduced.

The tarball is `git archive` of the two pinned commits, unchanged (no file edited or left out), packed sorted with a fixed owner and mtime and `gzip -n`, so the same pins give the same bytes. `scripts/make-renderer-tarball.sh REIMS_CLONE M2V_CLONE OUT.tar.gz` makes it and prints the sha256 that belongs in `pins.env`.

Our changes on top of the base:

- reims-vgpu: count dma-buf host-memory import (`4969eab6`), keep the root FIFO on an in-place resume and drain root first (`81e0125b`).
- metal2vulkan: framebuffer fetch and raster order groups without an interlock, no-copy buffers over dma-bufs on devices without host-pointer import, barrier before every fetching draw, runtime switches `M2V_NO_INTERLOCK` and `M2V_NO_HOSTPTR` (head `e0a40424`).

`reims-vgpu` expects `metal2vulkan` as a sibling directory (its Cargo.toml patches the git dependency to `../metal2vulkan`), which is how `snapintosh build` lays them out.

To build from a clone of your own instead, set `REIMS_REPO` and `M2V_REPO` (and `REIMS_REF`, `M2V_REF`, full commit ids).
