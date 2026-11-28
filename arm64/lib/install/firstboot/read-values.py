#!/usr/bin/env python3
"""Read a restored disk's LocalPolicy inputs and the aux image's nonce slots.

usage: read-values.py DISK AUX ECID [MOUNT]
Prints VUID, NSIH, SPIH, LPNH and RPNH as shell assignments. DISK and AUX are read only except for
the temporary Preboot mount under MOUNT. fsapfs tools and sgdisk are needed (libfsapfs-utils and gdisk).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

MARKER = b"boop-storage-nonces="
PART_CONTAINER = "AF0A"
SUDO = [] if os.geteuid() == 0 else ["sudo", "-n"]


def tool(name):
    """An absolute path: sudo's secure_path on a cloud image does not always carry this tool's directory."""
    return shutil.which(name) or name


def die(message):
    raise SystemExit(message)


def run(argv):
    try:
        return subprocess.run(SUDO + argv, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        die("%s: %s" % (" ".join(argv), getattr(exc, "stderr", exc)))


def main_container_offset(disk):
    out = run([tool("sgdisk"), "-p", disk]).stdout
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[-1] == "Container" and fields[-2] == PART_CONTAINER:
            return int(fields[1]) * 512
    die("no main APFS Container partition found in %s" % disk)


def preboot_index(disk, offset):
    proc = subprocess.run(SUDO + [tool("fsapfsinfo"), "-o", str(offset), disk],
                          capture_output=True, text=True, check=True)
    current = None
    for line in (proc.stdout + proc.stderr).splitlines():
        match = re.match(r"Volume: (\d+) information:", line.strip())
        if match:
            current = int(match.group(1))
        match = re.match(r"Name\s*:\s*(\S+)", line.strip())
        if match and match.group(1) == "Preboot" and current is not None:
            return current
    die("no Preboot volume in the container at %d" % offset)


def sha384_file(path):
    digest = hashlib.sha384()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def disk_values(disk, ecid, mount):
    offset = main_container_offset(disk)
    index = preboot_index(disk, offset)
    os.makedirs(mount, exist_ok=True)
    # keep the fsapfsmount banner off stdout: the caller sources this script's output as shell assignments
    proc = subprocess.Popen(SUDO + [tool("fsapfsmount"), "-f", str(index), "-o", str(offset), "-X", "allow_other", disk, mount],
                            stdout=sys.stderr)
    try:
        for _ in range(60):
            if os.listdir(mount):
                break
            time.sleep(0.5)
        dirs = [name for name in os.listdir(mount)
                if re.fullmatch(r"[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", name)]
        if len(dirs) != 1:
            die("expected one volume-group directory in Preboot, got %r" % dirs)
        vuid = dirs[0].upper()
        boot_dir = os.path.join(mount, vuid, "boot")
        boots = [name for name in os.listdir(boot_dir) if re.fullmatch(r"[0-9A-Fa-f]{96}", name)]
        if not boots:
            die("no boot/<nsih> directory in Preboot")
        nsih = boots[0].upper()
        ticket = os.path.join(mount, vuid, "cryptex1", "current", "apticket.vma2macosap.%016X.im4m" % ecid)
        if not os.path.isfile(ticket):
            die("no cryptex apticket for ECID at %s" % ticket)
        spih = sha384_file(ticket)
    finally:
        subprocess.run(SUDO + ["umount", mount], capture_output=True)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return vuid, nsih, spih


def aux_values(aux):
    data = open(aux, "rb").read()
    starts = [match.start() + len(MARKER) for match in re.finditer(re.escape(MARKER), data)]
    if not starts:
        die("no boop-storage-nonces marker in %s" % aux)
    chosen = next((start for start in starts if start + 112 <= len(data)), None)
    if chosen is None:
        die("no complete nonce bank in %s" % aux)
    slot0 = data[chosen:chosen + 16]
    slot1 = data[chosen + 16:chosen + 32]
    return hashlib.sha384(slot0).hexdigest(), hashlib.sha384(slot1).hexdigest()


def main():
    if len(sys.argv) < 4:
        die("usage: read-values.py DISK AUX ECID [MOUNT]")
    disk, aux, ecid = sys.argv[1], sys.argv[2], int(sys.argv[3])
    mount = sys.argv[4] if len(sys.argv) > 4 else os.path.join(os.path.dirname(os.path.abspath(aux)), "preboot")
    vuid, nsih, spih = disk_values(disk, ecid, mount)
    rpnh, lpnh = aux_values(aux)
    for name, value in (("VUID", vuid), ("NSIH", nsih), ("SPIH", spih), ("LPNH", lpnh), ("RPNH", rpnh)):
        print("%s=%s" % (name, value))


if __name__ == "__main__":
    main()
