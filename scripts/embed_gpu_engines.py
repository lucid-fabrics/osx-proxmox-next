#!/usr/bin/env python3
"""Refresh the verbatim copies of the Python GPU engines inside the bash installer.

Run after changing host_setup.py, bios_guide.py, data/gpu/bios_paths.json,
data/gpu/amd_devices.txt or data/gpu/oc_boot_args.py:

    python3 scripts/embed_gpu_engines.py

tests/test_gpu_parity.py fails when a copy is stale.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "osx_proxmox_next"
BASH = REPO / "scripts" / "bash" / "osx-proxmox-next.sh"
EMBEDS = (
    ("host_setup.py", "GPU_EMBED_HOST_SETUP", PKG / "host_setup.py"),
    ("bios_guide.py", "GPU_EMBED_BIOS_GUIDE", PKG / "bios_guide.py"),
    ("bios_paths.json", "GPU_EMBED_BIOS_PATHS", PKG / "data/gpu/bios_paths.json"),
    ("amd_devices.txt", "GPU_EMBED_AMD_DEVICES", PKG / "data/gpu/amd_devices.txt"),
    ("oc_boot_args.py", "GPU_EMBED_OC_BOOT_ARGS", PKG / "data/gpu/oc_boot_args.py"),
)


def render(label: str, var: str, source: Path) -> str:
    text = source.read_text()
    end = var + "_EOF"
    assert end not in text, f"{source} contains its own heredoc delimiter"
    body = text if text.endswith("\n") else text + "\n"
    return (f"# ── GPU embedded: {label} begin ──\n"
            f"IFS= read -r -d '' {var} <<'{end}' || true\n{body}{end}\n"
            f"# ── GPU embedded: {label} end ──\n")


def main() -> int:
    script = BASH.read_text()
    for label, var, source in EMBEDS:
        pattern = re.compile(rf"# ── GPU embedded: {re.escape(label)} begin ──\n.*?# ── GPU embedded: {re.escape(label)} end ──\n", re.S)
        if not pattern.search(script):
            print(f"marker for {label} not found in {BASH}", file=sys.stderr)
            return 1
        script = pattern.sub(lambda _m: render(label, var, source), script, count=1)
    BASH.write_text(script)
    return 0


if __name__ == "__main__":
    sys.exit(main())
