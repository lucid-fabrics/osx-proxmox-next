"""Read and replace kext bundles on a Mac's OpenCore disk (data/gpu/oc_kext.py runs on the mounted disk)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from shlex import quote as shquote

from .infrastructure import ProxmoxAdapter
from .planner import mount_oc_disk_script

__all__ = ["OcError", "bundle_of", "kext_script", "read_versions", "record_amd", "replace_kexts"]

_SRC_VAR = "OC_KEXT_PY"
_EOF = "OC_KEXT_PY_EOF"


class OcError(Exception):
    """The OpenCore disk could not be read or changed."""


def bundle_of(name: str) -> str:
    """`Lilu` becomes `Lilu.kext`."""
    return name if name.endswith(".kext") else name + ".kext"


def _source() -> str:
    return (resources.files("osx_proxmox_next") / "data/gpu/oc_kext.py").read_text()


def kext_script(vid: str, calls: list[list[str]]) -> str:
    """Bash that mounts the Mac's OpenCore disk (ide0) and runs oc_kext.py once per entry of *calls*. The script itself
    refuses unless the Mac is stopped, mounts read only when it only reads, and unmounts on exit (also on INT and TERM).
    Every argument is quoted; nothing comes from a VM name."""
    source = _source()
    assert _EOF not in source
    body = " && ".join('python3 -I -c "$%s" "$OC_MNT/EFI/OC" %s' % (_SRC_VAR, " ".join(shquote(a) for a in call))
                       for call in calls)
    return (mount_oc_disk_script(vid, ro=not any(c[0] == "replace" for c in calls), require_stopped=True)
            + f"{_SRC_VAR}=$(cat <<'{_EOF}'\n{source}{'' if source.endswith(chr(10)) else chr(10)}{_EOF}\n) && "
            + body + " && sync\n")


def read_versions(pve: ProxmoxAdapter, vmid: int, names: list[str]) -> dict[str, str]:
    """{name: version} of the kexts on the Mac's OpenCore disk, '' for one that is missing. The Mac must be stopped."""
    res = pve.run(["bash", "-c", kext_script(str(vmid), [["versions", *[bundle_of(n) for n in names]]])])
    line = next((l for l in reversed(res.output.splitlines()) if l.startswith("{")), "")
    if not res.ok or not line:
        raise OcError("Could not read the OpenCore disk: " + res.output.strip()[-300:])
    try:
        found = json.loads(line)
    except ValueError as exc:
        raise OcError("Unreadable answer from the OpenCore disk.") from exc
    return {n: str(found.get(bundle_of(n), "")) for n in names}


def replace_kexts(pve: ProxmoxAdapter, vmid: int, items: list[tuple[str, Path, str]],
                  notes: list[str] | None = None) -> dict[str, str]:
    """Replace each kext of *items* ((name, zip, sha256)) on the OpenCore disk. Returns {name: new version}. A NOTE of
    the replacer (a bundle that config.plist does not list is not loaded by OpenCore) is added to *notes*."""
    calls = [["replace", bundle_of(name), str(zip_path), sha] for name, zip_path, sha in items]
    res = pve.run(["bash", "-c", kext_script(str(vmid), calls)])
    if not res.ok:
        raise OcError("Replacing the kexts failed: " + res.output.strip()[-300:])
    lines = [l.strip() for l in res.output.splitlines()]
    versions = [l[len("VERSION "):] for l in lines if l.startswith("VERSION ")]
    if notes is not None:
        notes += [l[len("NOTE: "):] for l in lines if l.startswith("NOTE: ")]
    return {name: (versions[i] if i < len(versions) else "") for i, (name, _z, _s) in enumerate(items)}


def record_amd(pve: ProxmoxAdapter, vmid: int) -> bool:
    """Write the driver record of an AMD Mac from the Lilu and WhateverGreen on its OpenCore disk (the Mac must be
    stopped). Returns False, writing nothing, when the disk cannot be read: the record is a courtesy."""
    from . import driver_engine as engine
    try:
        found = read_versions(pve, vmid, ["Lilu", "WhateverGreen"])
        parts = [engine.component(n, v, v, "", "disk") for n, v in found.items() if v]
        if parts:
            engine.write_record(vmid, "amd", parts)
        return bool(parts)
    except (OcError, OSError, ValueError):
        return False
