#!/usr/bin/env python3
"""Offline unit tests for lib/sshwait.py with a fake clock and a fake ssh. Run: python3 -I tests/test_sshwait.py"""
import importlib.util, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("sshwait", os.path.join(HERE, "..", "lib", "sshwait.py"))
w = importlib.util.module_from_spec(sp); sp.loader.exec_module(w)


class Fake:
    def __init__(self, answers, t=0.0):
        self.t, self.answers, self.calls, self.timeouts = t, list(answers), [], []
    def clock(self): return self.t
    def sleep(self, s): self.t += s
    def run(self, argv, capture_output, text, timeout):
        self.calls.append((self.t, argv)); self.timeouts.append(timeout)
        dur, rc = self.answers.pop(0) if self.answers else (1, 255)
        if dur > timeout:
            self.t += timeout; raise subprocess.TimeoutExpired(argv, timeout)
        self.t += dur
        return subprocess.CompletedProcess(argv, rc, "up\n" if rc == 0 else "", "")


def go(f, **kw):
    return w.wait_ssh(["ssh", "-o", "ConnectTimeout=6", "-p", "1", "h"], "true", kw.pop("deadline", 400),
                      clock=f.clock, sleep=f.sleep, run=f.run, **kw)

# 1. ConnectTimeout is replaced by 120 and appears once
f = Fake([(1, 0)]); r, n = go(f)
assert r is not None and n == 1
a = f.calls[0][1]
assert a.count("-o") == 1 and "ConnectTimeout=120" in a and "ConnectTimeout=6" not in a and a[-1] == "true", a

# 2. first try waits for t_ref + first_at
f = Fake([(1, 0)]); r, n = go(f, first_at=45, t_ref=0.0)
assert f.calls[0][0] >= 45, f.calls

# 3. a slow login (30 s, longer than the old 6 s and 25 s kills) is not killed and wins
f = Fake([(1, 255), (30, 0)]); r, n = go(f, interval=2)
assert r is not None and n == 2 and f.timeouts[1] > 100, (n, f.timeouts)

# 4. a try gets the whole time left to the deadline, never a short kill
f = Fake([(1000, 0)]); r, n = go(f, deadline=200)
assert r is None and n == 1 and f.timeouts[0] == 200 and f.t == 200

# 5. only one try runs at a time and tries are spaced by interval
f = Fake([(1, 255)] * 5 + [(1, 0)]); r, n = go(f, interval=2)
starts = [c[0] for c in f.calls]
assert n == 6 and all(b - a >= 3 for a, b in zip(starts, starts[1:])), starts

# 6. the deadline ends the wait, no open-ended loop
f = Fake([]); r, n = go(f, deadline=60, interval=2)
assert r is None and f.t <= 62, f.t

# 7. alive() turning false stops the wait; ok() can veto an answer
f = Fake([(1, 255)] * 10); k = [0]
def alive():
    k[0] += 1; return k[0] < 3
r, n = go(f, alive=alive); assert r is None and n == 2
f = Fake([(1, 0), (1, 0)]); r, n = go(f, ok=lambda x: False, deadline=10); assert r is None
print("sshwait: all pass")
