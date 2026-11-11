#!/usr/bin/env python3
"""Refresh the verbatim copies of the Python GPU engines inside the bash installer.

Run after changing host_setup.py, bios_guide.py, data/gpu/bios_paths.json,
data/gpu/amd_devices.txt, data/gpu/oc_boot_args.py, data/nullmoth/oc_patch.py, nullmoth_engine.py, data/nullmoth/nm_* or any iGPU
file (igpu_engine.py, data/gpu/intel_devices.txt, oc_igpu.py, igpu_*.sh, igpu_launchd.plist, igpu_power_watchdog.py, igpu_diag.py, osx-next-power-watchdog@.service):

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
    ("intel_devices.txt", "GPU_EMBED_INTEL_DEVICES", PKG / "data/gpu/intel_devices.txt"),
    ("igpu_engine.py", "GPU_EMBED_IGPU_ENGINE", PKG / "igpu_engine.py"),
    ("oc_igpu.py", "GPU_EMBED_OC_IGPU", PKG / "data/gpu/oc_igpu.py"),
    ("igpu_firstboot.sh", "GPU_EMBED_IGPU_FIRSTBOOT", PKG / "data/gpu/igpu_firstboot.sh"),
    ("igpu_stage.sh", "GPU_EMBED_IGPU_STAGE", PKG / "data/gpu/igpu_stage.sh"),
    ("igpu_launchd.plist", "GPU_EMBED_IGPU_PLIST", PKG / "data/gpu/igpu_launchd.plist"),
    ("nullmoth_engine.py", "GPU_EMBED_NULLMOTH_ENGINE", PKG / "nullmoth_engine.py"),
    ("nm_stage.sh", "GPU_EMBED_NM_STAGE", PKG / "data/nullmoth/nm_stage.sh"),
    ("nm_firstboot.sh", "GPU_EMBED_NM_FIRSTBOOT", PKG / "data/nullmoth/nm_firstboot.sh"),
    ("nm_launchd.plist", "GPU_EMBED_NM_PLIST", PKG / "data/nullmoth/nm_launchd.plist"),
    ("igpu_power_watchdog.py", "GPU_EMBED_IGPU_WATCHDOG", PKG / "data/gpu/igpu_power_watchdog.py"),
    ("igpu_diag.py", "GPU_EMBED_IGPU_DIAG", PKG / "data/gpu/igpu_diag.py"),
    ("osx-next-power-watchdog@.service", "GPU_EMBED_IGPU_WATCHDOG_UNIT", PKG / "data/gpu/osx-next-power-watchdog@.service"),
)


def render(label: str, var: str, source: Path) -> str:
    text = source.read_text()
    end = var + "_EOF"
    assert end not in text, f"{source} contains its own heredoc delimiter"
    body = text if text.endswith("\n") else text + "\n"
    return (f"# ── GPU embedded: {label} begin ──\n"
            f"IFS= read -r -d '' {var} <<'{end}' || true\n{body}{end}\n"
            f"# ── GPU embedded: {label} end ──\n")


OC_PATCH = PKG / "data/nullmoth/oc_patch.py"
_OC_PATCH_RE = re.compile(r"(<<'NULLMOTH_PY'\n).*?(\nNULLMOTH_PY\n)", re.S)


def main() -> int:
    script = BASH.read_text()
    if not _OC_PATCH_RE.search(script):
        print(f"NULLMOTH_PY heredoc not found in {BASH}", file=sys.stderr)
        return 1
    body = OC_PATCH.read_text()
    script = _OC_PATCH_RE.sub(lambda m: m.group(1) + body.rstrip("\n") + m.group(2), script, count=1)
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
