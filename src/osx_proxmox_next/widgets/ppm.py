"""Read a QEMU screendump (binary PPM, P6) and sample it down to terminal cells.

Pure Python, no dependency. A 1920x1080 frame is 6 MB, so nothing here walks
every pixel: the sampler reads a small fixed number of pixels per output pixel."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Frame", "parse_ppm", "fit_size", "sample", "Pixels"]

Pixels = list[list[tuple[int, int, int]]]
_WHITESPACE = b" \n\r\t"


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    data: bytes
    offset: int  # where the pixel bytes start in *data*


def _token(data: bytes, pos: int) -> tuple[bytes, int]:
    """Next whitespace-delimited header token (comment lines skipped) and the position after it."""
    while pos < len(data):
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] != b"\n":
                pos += 1
        elif data[pos:pos + 1] in _WHITESPACE:
            pos += 1
        else:
            break
    end = pos
    while end < len(data) and data[end:end + 1] not in _WHITESPACE and data[end:end + 1] != b"":
        end += 1
    return data[pos:end], end


def parse_ppm(data: bytes) -> Frame | None:
    """The Frame of a binary 8-bit PPM, or None when *data* is not a complete one."""
    magic, pos = _token(data, 0)
    if magic != b"P6":
        return None
    fields = []
    for _ in range(3):
        token, pos = _token(data, pos)
        if not token.isdigit():
            return None
        fields.append(int(token))
    width, height, maxval = fields
    if width <= 0 or height <= 0 or maxval != 255:
        return None
    offset = pos + 1  # exactly one whitespace byte follows maxval
    if len(data) - offset < width * height * 3:
        return None
    return Frame(width, height, data, offset)


def fit_size(src_w: int, src_h: int, cols: int, rows: int) -> tuple[int, int]:
    """Largest (w, h) in pixels that keeps the aspect ratio inside cols x rows pixels."""
    if cols <= 0 or rows <= 0:
        return 0, 0
    w = cols
    h = max(1, round(cols * src_h / src_w))
    if h > rows:
        h = rows
        w = max(1, round(rows * src_w / src_h))
    return w, h


def sample(frame: Frame, out_w: int, out_h: int) -> Pixels:
    """out_h rows of out_w (r, g, b) pixels: each averages up to 4 source pixels around its centre."""
    data, offset, width, height = frame.data, frame.offset, frame.width, frame.height
    rows: Pixels = []
    for oy in range(out_h):
        y0 = int(oy * height / out_h)
        y1 = min(height - 1, max(y0, int((oy + 1) * height / out_h) - 1))
        ys = (y0, y1) if y1 > y0 else (y0,)
        row = []
        for ox in range(out_w):
            x0 = int(ox * width / out_w)
            x1 = min(width - 1, max(x0, int((ox + 1) * width / out_w) - 1))
            xs = (x0, x1) if x1 > x0 else (x0,)
            r = g = b = 0
            for y in ys:
                base = offset + y * width * 3
                for x in xs:
                    i = base + x * 3
                    r += data[i]
                    g += data[i + 1]
                    b += data[i + 2]
            n = len(ys) * len(xs)
            row.append((r // n, g // n, b // n))
        rows.append(row)
    return rows
