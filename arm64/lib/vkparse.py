#!/usr/bin/env python3
"""vkparse.py EXT...: read `vulkaninfo` text on stdin, print one TSV line per physical device:
index, name, type, vendorID, missing required extensions (space separated, empty if none)."""
import re
import sys

required = sys.argv[1:]
text = sys.stdin.read()
blocks = re.split(r"^GPU(\d+):\s*$", text, flags=re.M)
# blocks = [preamble, idx, body, idx, body, ...]
for i in range(1, len(blocks) - 1, 2):
    idx, body = blocks[i], blocks[i + 1]

    def field(name, default="?"):
        m = re.search(r"^\s*%s\s*=\s*(.+?)\s*$" % name, body, flags=re.M)
        return m.group(1) if m else default

    name = field("deviceName")
    vendor = field("vendorID", "?").split()[0].lower()
    dtype = field("deviceType").replace("PHYSICAL_DEVICE_TYPE_", "").lower()
    exts = set(re.findall(r"^\s*(VK_[A-Za-z0-9_]+)\s*:\s*extension revision", body, flags=re.M))
    missing = " ".join(e for e in required if e not in exts)
    print("\t".join([idx, name, dtype, vendor, missing]))
