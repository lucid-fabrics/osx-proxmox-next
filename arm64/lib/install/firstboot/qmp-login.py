#!/usr/bin/env python3
"""Type a login password at the guest's login window over QMP.

usage: qmp-login.py QMP_SOCKET PASSWORD
Only the characters the login window needs for the supported password alphabet are typed: lower case
letters, digits, and a small set of punctuation. Upper case uses a simultaneous Shift press.
"""
from __future__ import annotations

import json
import socket
import sys
import time

QCODES = {
    "-": "minus", "=": "equal", ".": "dot", ",": "comma", "/": "slash", " ": "spc", "_": "shift-minus",
}


def qmp(stream, name, **args):
    stream.write(json.dumps({"execute": name, "arguments": args}) + "\n")
    stream.flush()
    while True:
        reply = json.loads(stream.readline())
        if "return" in reply:
            return reply["return"]
        if "error" in reply:
            raise RuntimeError("%s: %s" % (name, reply["error"]))


def key_for(char):
    if char.isascii() and (char.islower() or char.isdigit()):
        return [{"type": "qcode", "data": char}]
    if char.isascii() and char.isupper():
        return [{"type": "qcode", "data": "shift"}, {"type": "qcode", "data": char.lower()}]
    if char in QCODES:
        code = QCODES[char]
        return [{"type": "qcode", "data": code}]
    raise ValueError("password character %r is not supported" % char)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: qmp-login.py QMP_SOCKET PASSWORD")
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(60)
    connection.connect(sys.argv[1])
    stream = connection.makefile("rw")
    stream.readline()
    qmp(stream, "qmp_capabilities")
    # the user-selection screen needs a click before the password field exists; on the password screen the
    # same point is the field or below it, harmless. 16384/32767 of the width, 27307/32767 of the height.
    for event in ({"type": "abs", "data": {"axis": "x", "value": 16384}},
                  {"type": "abs", "data": {"axis": "y", "value": 27307}},
                  {"type": "btn", "data": {"down": True, "button": "left"}},
                  {"type": "btn", "data": {"down": False, "button": "left"}}):
        qmp(stream, "input-send-event", events=[event])
    time.sleep(3)   # the password field opens after the click; typing before it exists loses the password
    for _ in range(64):   # clear a field a previous try or the user left focused
        qmp(stream, "send-key", keys=[{"type": "qcode", "data": "backspace"}])
    for char in sys.argv[2]:
        qmp(stream, "send-key", keys=key_for(char))
        time.sleep(0.2)
    qmp(stream, "send-key", keys=[{"type": "qcode", "data": "ret"}])


if __name__ == "__main__":
    main()
