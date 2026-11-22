"""Turn a PNG into the binary PPM (P6) a QEMU screendump is, so a demo frame takes the app's own PPM path.

Standard library only (zlib), 8-bit RGB or RGBA, not interlaced. The picture is thinned to at most *max_width*
pixels across: the live screen of the app samples it down to a few dozen cells anyway."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _chunks(data: bytes):
    pos = 8
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        yield kind, data[pos + 8:pos + 8 + size]
        pos += 12 + size


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> list[bytearray]:
    stride = width * bpp
    rows: list[bytearray] = []
    prev = bytearray(stride)
    for y in range(height):
        base = y * (stride + 1)
        kind, line = raw[base], bytearray(raw[base + 1:base + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return rows


def png_to_ppm(path: Path, max_width: int = 640) -> bytes:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    idat = b""
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            width, height, depth, color, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError("only 8-bit RGB or RGBA PNGs without interlacing are supported")
    bpp = 3 if color == 2 else 4
    rows = _unfilter(zlib.decompress(idat), width, height, bpp)
    step = max(1, -(-width // max_width))
    out_w, out_h = len(range(0, width, step)), len(range(0, height, step))
    pixels = bytearray()
    for y in range(0, height, step):
        row = rows[y]
        for x in range(0, width * bpp, step * bpp):
            pixels += row[x:x + 3]
    return b"P6\n%d %d\n255\n" % (out_w, out_h) + bytes(pixels)
