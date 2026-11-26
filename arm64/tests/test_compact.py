#!/usr/bin/env python3
"""Offline test for lib/compact.sh. A pack whose flock is held for 3 s (the exiting QEMU) and a fake compactor that answers like
qemu-pcache-compact: rc 2 "in use" while the lock is held, else rc 0 "compacted". Run: python3 -I tests/test_compact.py"""
import fcntl, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "lib", "compact.sh")
T = tempfile.mkdtemp()
pack, tool = os.path.join(T, "x.pack"), os.path.join(T, "fake-compact")
open(pack, "w").close()
open(tool, "w").write('#!/bin/sh\nif flock -n "$1" true; then echo "$1: compacted"; exit 0; fi\necho "$1: in use"; exit 2\n')
os.chmod(tool, 0o755)


def run(wait, hold):
    fd = os.open(pack, os.O_RDWR); fcntl.flock(fd, fcntl.LOCK_EX)
    pid = os.fork()
    if pid == 0:  # releases the lock after `hold` seconds, like QEMU finishing its exit
        time.sleep(hold); os.close(fd); os._exit(0)
    os.close(fd)  # the child's copy keeps the lock
    r = subprocess.run(["sh", SCRIPT, pack, tool], capture_output=True, text=True, env=dict(os.environ, PCCOMPACTWAIT=str(wait)))
    os.waitpid(pid, 0)
    return r

# 1. before the fix (no wait): the compactor gives up at once with rc 2
r = run(0, 2); assert r.returncode == 2 and "in use" in r.stdout and "(rc=2) waited=0s" in r.stdout, r.stdout
# 2. with the bounded wait: the lock frees after 2 s, the compactor runs, rc 0, one result line only
r = run(10, 2); assert r.returncode == 0 and "compacted" in r.stdout and "in use" not in r.stdout, r.stdout
assert "compact start" in r.stdout and "compacted (rc=0) waited=" in r.stdout
# 3. a lock that is never released: gives up after the bound, rc 2
r = run(2, 6); assert r.returncode == 2 and "waited=2s" in r.stdout and "pack in use" in r.stdout, r.stdout
# 4. nothing to do (rc 1) is said in words, still exits 1 (documented: 0 done, 1 nothing to do, 2 in use, 3 error); an error says FAILED
for code, word in ((1, "nothing to do, not an error"), (3, "FAILED")):
    open(tool, "w").write("#!/bin/sh\necho x; exit %d\n" % code)
    r = subprocess.run(["sh", SCRIPT, pack, tool], capture_output=True, text=True)
    assert r.returncode == code and word in r.stdout.splitlines()[-1] and ("(rc=%d)" % code) in r.stdout, r.stdout
print("compact tests ok")
