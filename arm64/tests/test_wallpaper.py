#!/usr/bin/env python3
"""Offline replay of saved screenshots through snapctl's "desktop is drawn" check. Run: python3 -I tests/test_wallpaper.py
Fixtures (tests/fixtures/screens, gzip ppm): black = VM screen before the desktop (p18), wallpaper = a drawn desktop (p42, scaled to
720x540), safari = a real S7 screendump with a full-size Safari window (346 sparse colours, the case that stalled WALLWAIT)."""
import gzip, importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("snapctl", os.path.join(HERE, "..", "lib", "snapctl.py"))
s = importlib.util.module_from_spec(sp); sp.loader.exec_module(s)


def load(name):
    return s.parse_ppm(gzip.open(os.path.join(HERE, "fixtures", "screens", name + ".ppm.gz")).read())


def cover(w, h, px, x0, y0, x1, y1, rgb=(0, 255, 200)):
    """a flat window over the rectangle (fractions of the screen)"""
    b = bytearray(px)
    row = bytes(rgb) * (int(w * x1) - int(w * x0))
    for y in range(int(h * y0), int(h * y1)):
        o = (y * w + int(w * x0)) * 3
        b[o:o + len(row)] = row
    return w, h, bytes(b)


fails = 0
def check(label, frame, want):
    global fails
    c = s.colour_counts(*frame); got = s.drawn(c)
    print("%-34s full %5d edge %6d drawn %-5s %s" % (label, c[0], c[1], got, "ok" if got == want else "FAIL"))
    if got != want: fails += 1

black, wall, saf = load("black"), load("wallpaper"), load("safari")
check("black screen", black, False)
check("wallpaper, no window", wall, True)
check("real Safari-covered screen (S7)", saf, True)
check("wallpaper, window 92x87 %", cover(*wall, .04, .03, .96, .90), True)
check("wallpaper, window over left half", cover(*wall, 0, .03, .60, .90), True)
check("wallpaper, window over right half", cover(*wall, .40, .03, 1, .90), True)
check("black, window over most of it", cover(*black, .04, .03, .96, .90), False)
assert s.drawn(None) is False
sys.exit(1 if fails else 0)
