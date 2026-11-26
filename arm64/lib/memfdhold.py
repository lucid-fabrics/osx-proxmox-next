#!/usr/bin/env python3
"""memfdhold.py SIZE PATHFILE SECS: make a sealed (F_SEAL_SHRINK) memfd of SIZE (e.g. 8G), write its /proc/PID/fd/N path to PATHFILE, hold it SECS seconds.
QEMU opens that path with memory-backend-file (align=2M), so guest RAM is 16 KiB aligned for reims-vgpu and a memfd for /dev/udmabuf."""
import fcntl, os, sys, time
s = sys.argv[1]; n = int(s[:-1]) * {"M": 1 << 20, "G": 1 << 30}[s[-1]]
fd = os.memfd_create("g2-guest-ram", os.MFD_ALLOW_SEALING)
os.ftruncate(fd, n)
fcntl.fcntl(fd, 1033, 0x2)  # F_ADD_SEALS, F_SEAL_SHRINK
open(sys.argv[2], "w").write("/proc/%d/fd/%d" % (os.getpid(), fd))
time.sleep(int(sys.argv[3]))
