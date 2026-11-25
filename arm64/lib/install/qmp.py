#!/usr/bin/env python3
# M2b: minimal QMP client (standard library only) and the vmapple boot-nonce reader.
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# The vmapple machine draws a 32-bit random value `rnd` at init and stores it in the
# vmapple-cfg device. Ambrosia puts it in /chosen boot-nonce, four little-endian bytes
# repeated eight times, which is the AP nonce AppleImage4 checks tickets against
# ("Image4: using the ApBootNonce"). The host reads `rnd` over QMP.
import json
import socket
import struct
import time


class Qmp:
    def __init__(self, path, timeout=60.0):
        deadline = time.monotonic() + timeout
        while True:
            self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                self.s.connect(path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                self.s.close()
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        self.f = self.s.makefile("rw")
        self.f.readline()  # greeting
        self.cmd("qmp_capabilities")

    def cmd(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            r = json.loads(self.f.readline())
            if "return" in r:
                return r["return"]
            if "error" in r:
                raise RuntimeError("%s: %s" % (name, r["error"]))

    def close(self):
        self.s.close()


def find_cfg(q, base="/machine/unattached"):
    for e in q.cmd("qom-list", path=base):
        if e["type"].startswith("child<vmapple-cfg>"):
            return base + "/" + e["name"]
    for e in q.cmd("qom-list", path=base):
        if e["type"].startswith("child<"):
            pass
    raise RuntimeError("vmapple-cfg not found under " + base)


def boot_nonce(path):
    q = Qmp(path)
    try:
        cfg = find_cfg(q)
        rnd = q.cmd("qom-get", path=cfg, property="rnd")
    finally:
        q.close()
    return struct.pack("<I", rnd) * 8


if __name__ == "__main__":
    import sys

    print(boot_nonce(sys.argv[1]).hex())
