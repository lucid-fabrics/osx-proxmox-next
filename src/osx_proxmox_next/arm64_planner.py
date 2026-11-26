"""Plans for the macOS 27 arm64 (beta) runner VM: plain `qm` and shell steps, nothing Snapintosh-specific.

The runner is a normal Proxmox VM (a Debian 13 cloud image, visible in the Proxmox UI). Every step is an argv list;
values that reach a shell are passed as positional arguments, never spliced into the script text."""

from __future__ import annotations

from .arm64 import (
    ARM64_TAG,
    KEY_DIR,
    DEBIAN_IMAGE_URL,
    DEBIAN_SUMS_URL,
    MAX_VMID,
    MIN_VMID,
    RUNNER_USER,
    Arm64Config,
    validate_arm64_config,
)
from .arm64_cloudinit import render_cloud_init
from .domain import PlanStep
from .planner import build_destroy_plan

__all__ = ["IMAGE_DIR", "SNIPPET_DIR", "build_arm64_destroy_plan", "build_arm64_plan", "host_key_path", "key_path", "snippet_path"]

IMAGE_DIR = "/var/lib/vz/template/osx-next-arm64"
SNIPPET_DIR = "/var/lib/vz/snippets"
_SNIPPET_STORAGE = "local"

_FETCH_IMAGE = """\
set -euo pipefail
url=$1; sums=$2; dest=$3; name=$(basename "$url")
want=$(curl -fsSL --retry 3 "$sums" | awk -v n="$name" '$2 == n { print $1 }')
[ -n "$want" ] || { echo "no checksum for $name in $sums" >&2; exit 1; }
# "latest" moves: a cached image that no longer matches the published checksum is replaced, never used
if [ -s "$dest" ] && ! echo "$want  $dest" | sha512sum -c --status -; then
  echo "the cached $name is out of date, downloading it again"
  rm -f "$dest"
fi
if [ ! -s "$dest" ]; then
  rm -f "$dest.part"
  curl -fL --retry 3 -o "$dest.part" "$url"
  mv "$dest.part" "$dest"
fi
echo "$want  $dest" | sha512sum -c -
"""

_CHECK_TAG = """\
set -euo pipefail
tags=$(qm config "$1" | sed -n 's/^tags: *//p' | tr ',; ' '\\n\\n\\n')
printf '%s\\n' "$tags" | grep -qx -- "$2" \\
  || { echo "VM $1 does not carry the $2 tag, so it was not made by this wizard: refusing to touch it" >&2; exit 1; }
"""

_MAKE_KEY = """\
set -euo pipefail
priv=$1; out=$2
mkdir -p "$(dirname "$priv")"; chmod 700 "$(dirname "$priv")"
[ -f "$priv" ] || ssh-keygen -q -t ed25519 -N '' -C "$3" -f "$priv"
( umask 077; cat "$priv.pub" > "$out" )
chmod 600 "$out"
"""

_CHECK_SNIPPETS = """\
set -euo pipefail
pvesm status --content snippets | awk 'NR > 1 { print $1 }' | grep -qx "$1" \\
  || { echo "storage $1 does not allow snippets: enable the Snippets content type on it, then run again" >&2; exit 1; }
"""

_WRITE_FILE = 'set -euo pipefail\nprintf "%s" "$1" > "$2"\n'
_WRITE_PRIVATE = 'set -euo pipefail\n( umask 077; printf "%s\\n" "$1" > "$2" )\nchmod 600 "$2"\n'


def snippet_path(vmid: int) -> str:
    return f"{SNIPPET_DIR}/osx-next-arm64-{vmid}.yaml"


def key_path(vmid: int) -> str:
    """The file `qm set --sshkeys` reads the runner's public key from (the CLI takes a path, only the API takes the key)."""
    return f"{SNIPPET_DIR}/osx-next-arm64-{vmid}.pub"


def host_key_path(vmid: int) -> str:
    """The private half of the runner's login key, kept on the Proxmox host: `ssh -i` it to log in."""
    return f"{KEY_DIR}/osx-next-arm64-{vmid}"


def _sh(title: str, script: str, *args: str, risk: str = "safe") -> PlanStep:
    return PlanStep(title=title, argv=["bash", "-c", script, "_", *args], risk=risk)


def _net0(config: Arm64Config) -> str:
    tag = f",tag={config.vlan}" if config.vlan else ""
    return f"virtio,bridge={config.bridge}{tag}"


def build_arm64_plan(config: Arm64Config) -> list[PlanStep]:
    issues = validate_arm64_config(config)
    if issues:
        raise ValueError(f"Invalid arm64 config: {'; '.join(issues)}")
    vmid = str(config.vmid)
    image = f"{IMAGE_DIR}/{DEBIAN_IMAGE_URL.rsplit('/', 1)[1]}"
    create = [
        "qm", "create", vmid, "--name", config.name, "--memory", str(config.memory_mb),
        "--cores", str(config.cores), "--cpu", "host", "--ostype", "l26", "--machine", "q35",
        "--scsihw", "virtio-scsi-single", "--net0", _net0(config), "--serial0", "socket", "--vga", "serial0",
        "--agent", "enabled=1", "--tags", ARM64_TAG, "--onboot", "0",
    ]
    if config.cpu_units:
        create += ["--cpuunits", str(config.cpu_units)]
    cloud = ["qm", "set", vmid, "--ide2", f"{config.storage}:cloudinit", "--boot", "order=scsi0",
             "--ciuser", RUNNER_USER, "--ipconfig0", "ip=dhcp",
             "--cicustom", f"vendor=local:snippets/osx-next-arm64-{vmid}.yaml",
             "--sshkeys", key_path(config.vmid)]
    steps = [
        _sh("Check snippet storage", _CHECK_SNIPPETS, _SNIPPET_STORAGE),
        PlanStep(title="Make the image folder", argv=["mkdir", "-p", IMAGE_DIR]),
        _sh("Fetch the Debian 13 cloud image", _FETCH_IMAGE, DEBIAN_IMAGE_URL, DEBIAN_SUMS_URL, image),
        PlanStep(title="Create the runner VM", argv=create),
        PlanStep(title="Import the disk", argv=[
            "qm", "set", vmid, "--scsi0", f"{config.storage}:0,import-from={image},discard=on,ssd=1,iothread=1"]),
        PlanStep(title="Grow the disk", argv=["qm", "resize", vmid, "scsi0", f"{config.disk_gb}G"]),
        _sh("Write the cloud-init file", _WRITE_FILE, render_cloud_init(config), snippet_path(config.vmid)),
    ]
    if config.ssh_public_key:
        steps.append(_sh("Write the SSH key file", _WRITE_PRIVATE, config.ssh_public_key, key_path(config.vmid)))
    else:   # every runner gets a login: a key made here, its private half stays in KEY_DIR on the host
        steps.append(_sh("Make the runner's SSH key", _MAKE_KEY, host_key_path(config.vmid), key_path(config.vmid),
                         f"osx-next-arm64-{config.vmid}"))
    steps.append(PlanStep(title="Set up cloud-init", argv=cloud))
    if config.gpu_pci:
        steps.append(PlanStep(title="Pass the GPU to the runner",
                              argv=["qm", "set", vmid, "--hostpci0", f"{config.gpu_pci},pcie=1"], risk="warn"))
    steps.append(PlanStep(title="Start the runner", argv=["qm", "start", vmid]))
    return steps


def build_arm64_destroy_plan(vmid: int) -> list[PlanStep]:
    """The existing destroy plan with its disks purged, plus the runner's cloud-init file and the cached image."""
    if not MIN_VMID <= vmid <= MAX_VMID:
        raise ValueError(f"VMID must be between {MIN_VMID} and {MAX_VMID}.")
    return [_sh("Check the VM is a runner made by this wizard", _CHECK_TAG, str(vmid), ARM64_TAG),
            *build_destroy_plan(vmid, purge=True),
            PlanStep(title="Remove the cloud-init files", argv=["rm", "-f", snippet_path(vmid), key_path(vmid),
                                                               host_key_path(vmid), f"{host_key_path(vmid)}.pub"], risk="warn"),
            PlanStep(title="Remove the downloaded image", argv=["rm", "-rf", IMAGE_DIR], risk="warn")]
