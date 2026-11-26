#!/usr/bin/env python3
# M2b: rewrite an Ambrosia restore bundle (cpio newc) with real members.
#
# SPDX-License-Identifier: GPL-2.0-or-later
#
# usage: mkbundle.py IN.cpio OUT.cpio NAME=FILE [NAME=FILE ...]
# Replaces the named members (apticket.der, restore-policy.img4, nonce, boot-args, ...) and
# keeps every other member and the order. The bundle layout is Ambrosia's docs/restore.md.
import sys


def parse(b):
    out, o = [], 0
    while True:
        assert b[o : o + 6] == b"070701", "not a newc cpio"
        f = [int(b[o + 6 + 8 * i : o + 14 + 8 * i], 16) for i in range(13)]
        size, nsz = f[6], f[11]
        name = b[o + 110 : o + 110 + nsz - 1].decode()
        o += 110 + nsz
        o += -o % 4
        data = b[o : o + size]
        o += size
        o += -o % 4
        if name == "TRAILER!!!":
            return out
        out.append((name, data, f[1]))


def newc(files):
    out = b""
    for ino, (name, data, mode) in enumerate(files + [("TRAILER!!!", b"", 0)], 1):
        nb = name.encode() + b"\0"
        hdr = "070701" + "".join(
            "%08X" % x for x in (ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0)
        )
        rec = hdr.encode() + nb
        rec += b"\0" * (-len(rec) % 4)
        rec += data + b"\0" * (-len(data) % 4)
        out += rec
    return out


def main():
    files = parse(open(sys.argv[1], "rb").read())
    repl = dict(a.split("=", 1) for a in sys.argv[3:])
    names = [n for n, _, _ in files]
    for k in repl:
        assert k in names, "no member " + k
    files = [(n, open(repl[n], "rb").read() if n in repl else d, m) for n, d, m in files]
    open(sys.argv[2], "wb").write(newc(files))
    print("members", [(n, len(d)) for n, d, _ in files])


if __name__ == "__main__":
    main()
