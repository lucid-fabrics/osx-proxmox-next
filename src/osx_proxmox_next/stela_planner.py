"""Plans for the macOS 27 arm64 via Stela (beta) VM: plain `qm` and shell steps, nothing spliced into a shell.

Stela is built from one pinned commit that is fetched at run time (see stela.py); none of its source is part of this
project. The VM is a normal
Proxmox VM (OVMF, q35). Its boot stick is a raw image file, handed to the guest as a USB mass-storage device."""

from __future__ import annotations

from pathlib import Path

from .domain import PlanStep
from .planner import build_destroy_plan
from .stela import (
    MAX_VMID,
    MIN_VMID,
    STELA_FORKS,
    STELA_VM_TAG,
    StelaConfig,
    mac_for,
    tap_for,
    validate_stela_config,
)

__all__ = [
    "SNIPPET_DIR", "STICK_DIR", "STICK_GB", "build_stela_destroy_plan", "build_stela_finish_plan", "build_stela_plan",
    "BUILD_SCRIPT", "BUILD_TIMEOUT", "hook_path", "hook_script",
    "stela_qemu_args", "stela_vm_options", "stela_binary", "stela_dir", "stick_path",
]

SNIPPET_DIR = "/var/lib/vz/snippets"
STICK_DIR = "/var/lib/vz/template/osx-next-stela"
STICK_GB = 32            # the boot stick: boot files, firmware and the installer's settings, sparse until written
_SNIPPET_STORAGE = "local"

_CHECK_SNIPPETS = """\
set -euo pipefail
pvesm status --content snippets | awk 'NR > 1 { print $1 }' | grep -qx "$1" \\
  || { echo "storage $1 does not allow snippets: enable the Snippets content type on it, then run again" >&2; exit 1; }
"""

# Stela writes the stick through a loop device: it wants the device size spelled as it prints it (decimal GB).
_MAKE_STICK = """\
set -euo pipefail
bin=$1; img=$2; gb=$3
mkdir -p "$(dirname "$img")"
rm -f "$img"; truncate -s "${gb}G" "$img"
loop=$(losetup -f --show -P "$img")
trap 'losetup -d "$loop"' EXIT
size=$(blockdev --getsize64 "$loop")
confirm=$(awk -v b="$size" 'BEGIN { printf "%.1fGB", b / 1000000000 }')
"$bin" create "$loop" --installer online --macos latest --wifi none --confirm-disk "$confirm" --yes
"""

_CHECK_FREE = """\
set -euo pipefail
if qm status "$1" >/dev/null 2>&1; then
  echo "VM $1 already exists: pick another VMID, or remove it first with: osx-next-cli stela uninstall --vmid $1 --execute" >&2
  exit 1
fi
"""

_WRITE_EXEC = 'set -euo pipefail\nprintf "%s" "$1" > "$2"\nchmod 755 "$2"\n'

_CHECK_TAG = """\
set -euo pipefail
tags=$(qm config "$1" | sed -n 's/^tags: *//p' | tr ',; ' '\\n\\n\\n')
printf '%s\\n' "$tags" | grep -qx -- "$2" \\
  || { echo "VM $1 does not carry the $2 tag, so it was not made by this wizard: refusing to touch it" >&2; exit 1; }
"""

BUILD_SCRIPT = str(Path(__file__).parent / "data" / "stela" / "build-stela.sh")
BUILD_TIMEOUT = 6 * 3600     # the first build compiles Mesa and LLVM: about an hour on 32 cores, longer on fewer


def stela_dir(commit: str) -> str:
    """Where the build of one commit is cached: the stela binary, its stela.efi, BUILD-INFO and build.log."""
    return f"{STICK_DIR}/stela-{commit[:12]}"


def stela_binary(commit: str) -> str:
    return f"{stela_dir(commit)}/stela"


def stick_path(vmid: int) -> str:
    return f"{STICK_DIR}/stick-{vmid}.img"


def hook_path(vmid: int) -> str:
    return f"{SNIPPET_DIR}/osx-next-stela-{vmid}.sh"


def hook_script(vmid: int, bridge: str) -> str:
    """Proxmox runs this before the VM starts and after it stops: it makes the VM's tap and removes it again."""
    tap = tap_for(vmid)
    return f"""#!/bin/bash
# osx-next stela VM {vmid}: the guest NIC's tap on {bridge}
case "$2" in
  pre-start)
    ip link show {tap} >/dev/null 2>&1 || ip tuntap add dev {tap} mode tap
    ip link set {tap} master {bridge}
    ip link set {tap} up
    ;;
  post-stop)
    ip link del {tap} 2>/dev/null || true
    ;;
esac
exit 0
"""


def stela_qemu_args(config: StelaConfig) -> str:
    """The extra QEMU arguments (`qm --args`): a USB keyboard and the boot stick on a USB controller, and the NIC.

    Stela only drives a PCIe NIC: Proxmox's own e1000e sits on a conventional PCI bridge and Stela refuses it
    (ASPM needs a PCIe capability), so the NIC is added here, behind a pcie-root-port, on a tap of its own."""
    tap = tap_for(config.vmid)
    return " ".join([
        "-cpu host,+invtsc",
        "-device qemu-xhci,id=xhc1 -device usb-kbd,bus=xhc1.0",
        f"-drive if=none,id=stick,format=raw,file={stick_path(config.vmid)}",
        "-device usb-storage,bus=xhc1.0,drive=stick,bootindex=0",
        f"-netdev tap,id=sn0,ifname={tap},script=no,downscript=no",
        "-device pcie-root-port,id=rpn,chassis=20,slot=20,bus=pcie.0",
        f"-device e1000e,netdev=sn0,bus=rpn,mac={mac_for(config.vmid)}",
    ])


def stela_vm_options(config: StelaConfig) -> list[str]:
    """The `qm create` options after the VMID, in one place so a test can read the whole VM."""
    vga = "none" if config.gpu_pci else config.vga
    return [
        "--name", config.name, "--memory", str(config.memory_mb), "--balloon", "0", "--cores", str(config.cores),
        "--cpu", "host", "--ostype", "other", "--machine", "q35", "--bios", "ovmf",
        "--efidisk0", f"{config.storage}:1,efitype=4m,pre-enrolled-keys=0",
        "--sata0", f"{config.storage}:{config.disk_gb}", "--boot", "order=sata0",
        "--scsihw", "virtio-scsi-single", "--vga", vga, "--serial0", "socket", "--onboot", "0",
        "--tags", STELA_VM_TAG, "--args", stela_qemu_args(config),
        "--hookscript", f"{_SNIPPET_STORAGE}:snippets/osx-next-stela-{config.vmid}.sh",
    ]


def _sh(title: str, script: str, *args: str, risk: str = "safe") -> PlanStep:
    return PlanStep(title=title, argv=["bash", "-c", script, "_", *args], risk=risk)


def build_stela_plan(config: StelaConfig) -> list[PlanStep]:
    issues = validate_stela_config(config)
    if issues:
        raise ValueError(f"Invalid Stela config: {'; '.join(issues)}")
    vmid = str(config.vmid)
    steps = [
        _sh("Check the VMID is free", _CHECK_FREE, vmid),
        _sh("Check snippet storage", _CHECK_SNIPPETS, _SNIPPET_STORAGE),
        PlanStep(title="Fetch and build Stela", timeout=BUILD_TIMEOUT, argv=[
            "bash", BUILD_SCRIPT, "host", config.stela_commit, stela_dir(config.stela_commit), config.storage,
            config.bridge, *([config.stela_url] if config.stela_url else []), *STELA_FORKS]),
        _sh("Make the boot stick", _MAKE_STICK, stela_binary(config.stela_commit), stick_path(config.vmid),
            str(STICK_GB)),
        _sh("Write the VM's hook script", _WRITE_EXEC, hook_script(config.vmid, config.bridge), hook_path(config.vmid)),
        PlanStep(title="Create the VM", argv=["qm", "create", vmid, *stela_vm_options(config)]),
    ]
    if config.gpu_pci:
        steps.append(PlanStep(title="Pass the GPU to the VM",
                              argv=["qm", "set", vmid, "--hostpci0", f"{config.gpu_pci},pcie=1,x-vga=1"], risk="warn"))
    steps.append(PlanStep(title="Start the VM", argv=["qm", "start", vmid]))
    return steps


def build_stela_finish_plan(vmid: int, gpu_pci: str) -> list[PlanStep]:
    """For a VM whose install finished without a GPU: add the card, drop the emulated display, start it again."""
    config = StelaConfig(vmid=vmid, name="finish", storage="x", gpu_pci=gpu_pci)
    issues = [i for i in validate_stela_config(config) if i.startswith(("VMID", "GPU"))]
    if issues or not gpu_pci:
        raise ValueError(f"Invalid finish request: {'; '.join(issues or ['a GPU address is required.'])}")
    vid = str(vmid)
    return [_sh("Check the VM was made by this wizard", _CHECK_TAG, vid, STELA_VM_TAG),
            PlanStep(title="Stop the VM", argv=["qm", "stop", vid], risk="warn"),
            PlanStep(title="Pass the GPU to the VM", argv=[
                "qm", "set", vid, "--vga", "none", "--hostpci0", f"{gpu_pci},pcie=1,x-vga=1"], risk="warn"),
            PlanStep(title="Start the VM", argv=["qm", "start", vid])]


def build_stela_destroy_plan(vmid: int) -> list[PlanStep]:
    """The existing destroy plan with its disks purged, plus the boot stick, the hook script and the tap."""
    if not MIN_VMID <= vmid <= MAX_VMID:
        raise ValueError(f"VMID must be between {MIN_VMID} and {MAX_VMID}.")
    return [_sh("Check the VM was made by this wizard", _CHECK_TAG, str(vmid), STELA_VM_TAG),
            *build_destroy_plan(vmid, purge=True),
            PlanStep(title="Remove the boot stick and hook script",
                     argv=["rm", "-f", stick_path(vmid), hook_path(vmid)], risk="warn"),
            _sh("Remove the VM's tap", 'ip link del "$1" 2>/dev/null || true', tap_for(vmid))]
