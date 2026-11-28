#!/usr/bin/env python3
"""Take a QMP screendump and reject a frame that is black or nearly empty.

usage: frame.py QMP_SOCKET OUT.ppm
Prints FRAME W H mean COLOURS and exits 3 when the frame is black (mean under 2.0) or has fewer than 8
sampled colours.
"""
from __future__ import annotations

import json
import socket
import sys


def qmp(stream, name, **args):
    stream.write(json.dumps({"execute": name, "arguments": args}) + "\n")
    stream.flush()
    while True:
        reply = json.loads(stream.readline())
        if "return" in reply:
            return reply["return"]
        if "error" in reply:
            raise RuntimeError("%s: %s" % (name, reply["error"]))


def stats(path):
    data = open(path, "rb").read()
    header = data.split(b"\n", 3)
    width, height = map(int, header[1].split())
    pixels = header[3]
    total = sum(pixels[0:len(pixels):3]) + sum(pixels[1:len(pixels):3]) + sum(pixels[2:len(pixels):3])
    stride = 3 * max(1, (len(pixels) // 3) // 100000)
    colours = len({pixels[i:i + 3] for i in range(0, len(pixels), stride)})
    return width, height, total / len(pixels), colours


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: frame.py QMP_SOCKET OUT.ppm")
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(120)
    connection.connect(sys.argv[1])
    stream = connection.makefile("rw")
    stream.readline()  # greeting
    qmp(stream, "qmp_capabilities")
    qmp(stream, "screendump", filename=sys.argv[2], format="ppm")
    width, height, mean, colours = stats(sys.argv[2])
    print("FRAME %d %d mean %.2f colours %d" % (width, height, mean, colours))
    if mean < 2.0 or colours < 8:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
