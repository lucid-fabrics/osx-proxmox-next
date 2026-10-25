# Experimental: arm64 macOS on Proxmox (Snapintosh in QEMU)

This folder is a research track, not a feature of osx-proxmox-next yet. The goal is
to boot Apple's arm64 macOS 27 on a regular x86 Proxmox host by running
[Snapintosh](https://github.com/Snapintosh/Snapintosh), an EL2 monitor written for
the ASUS Zenbook A16, inside an emulated arm64 machine (QEMU TCG).

Status: Phases 0 and 1 done. The macOS 27 kernel boots to a root shell under QEMU
TCG in about 22 seconds (see the progress log in [PLAN.md](PLAN.md)). No desktop yet.

## What makes it hard

On Intel macOS, the desktop can draw without a GPU, which is why the main
osx-proxmox-next tool works without passthrough. Apple Silicon macOS cannot:
WindowServer quits when it finds no Metal device. The plan adds a software GPU that
reuses Snapintosh's Metal driver front end and renders on the host through Mesa's
llvmpipe, so the guest sees a Metal device without any real GPU behind it.

Even when it works, the whole guest runs under emulation, so expect a desktop at a
few frames per second. This is about making it boot, not about daily use.

## Documents

- [PLAN.md](PLAN.md): phases, gates, kill criteria, timeline and risks.
- [ANALYSIS.md](ANALYSIS.md): every hardware dependency of Snapintosh and how it maps
  onto QEMU, with file and line references into the pinned sources.

## Licensing

Snapintosh is MIT with GPL-2.0 components. None of its code is copied into this
repository; any future scripts here fetch and build it from its own repositories.
Like the rest of this project, this track is for testing, lab use and learning.
Apple's license terms govern where macOS may run; you are responsible for compliance
in your region.
