#!/usr/bin/env python3
"""the end of golden phase 2 on a slow host. (1) the guest shutdown is a foreground command: the backgrounded `nohup shutdown ... &` died with
the ssh session and the guest never powered off. (2) the wait for QEMU to exit is a condition: it returns when QEMU is gone however long that takes
(up to the bound) and only then falls back to QMP quit. (3) the stay-awake guard needs no login session. Run: python3 -I tests/test_golden_shutdown.py"""
import importlib.util, json, os, socket, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("goldenctl", HERE + "/lib/goldenctl.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
T = tempfile.mkdtemp(prefix="sgs-")
fails = []


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        fails.append(name)


# 1. the shutdown command
seen = []
g.sh = lambda args, timeout: (seen.append(args[-1]), subprocess.CompletedProcess(args, 0, "", ""))[1]
g.guest_shutdown(["ssh", "x"])
cmd = seen[0]
check("shutdown-in-foreground", "shutdown -h now" in cmd and "&" not in cmd and "nohup" not in cmd, cmd)
check("shutdown-syncs-first", cmd.startswith("sync"), cmd)
check("guard-needs-no-session", "displaysleep 0" in g.GUARD and "askForPassword" in g.GUARD and "launchctl asuser" not in g.GUARD, g.GUARD)


# the guard is retried while sshd answers nothing, and stops at the deadline
tries = []
def flaky(args, timeout):
    tries.append(1)
    return subprocess.CompletedProcess(args, 0 if len(tries) >= 3 else 255, "guard-done" if len(tries) >= 3 else "", "banner")
g.sh = flaky
check("guard-retried-until-set", g.set_guard(["ssh"], time.time() + 30, lambda: True, pause=0) and len(tries) == 3, len(tries))
g.sh = lambda args, timeout: subprocess.CompletedProcess(args, 255, "", "banner")
check("guard-bounded-by-deadline", g.set_guard(["ssh"], time.time() + 1, lambda: True, pause=0.2) is False)


# 2. wait_exit: a QEMU that exits after 3 s is waited for (grace 30), no QMP; one that never exits gets the QMP quit after the grace
def rundir(name, secs):
    d = os.path.join(T, name); os.makedirs(d)
    p = subprocess.Popen(["sleep", str(secs)])
    open(d + "/qemu.pid", "w").write(str(p.pid))
    return d, p


d, p = rundir("a", 3)
threading.Thread(target=p.wait, daemon=True).start()   # reap it, as the shell does for the real QEMU
t = time.time(); g.wait_exit(d, grace=30)
check("waits-for-a-slow-exit", 2.5 < time.time() - t < 15 and p.poll() is not None, time.time() - t)

d, p = rundir("b", 60)
threading.Thread(target=p.wait, daemon=True).start()
got = []
srv = socket.socket(socket.AF_UNIX); srv.bind(d + "/qmp.sock"); srv.listen(1)
def qmp():
    c, _ = srv.accept(); f = c.makefile("rw"); f.write('{"QMP":{}}\n'); f.flush()
    for _ in range(2):
        m = json.loads(f.readline()); got.append(m["execute"]); f.write('{"return":{}}\n'); f.flush()
        if m["execute"] == "quit":
            p.terminate()
threading.Thread(target=qmp, daemon=True).start()
t = time.time(); g.wait_exit(d, grace=3)
check("qmp-quit-after-the-bound", got == ["qmp_capabilities", "quit"] and p.poll() is not None and 3 <= time.time() - t < 20, (got, time.time() - t))

os.environ["GOLDEN_SHUTDOWN_WAIT"] = "7"
d, p = rundir("c", 0)
p.wait()
t = time.time(); g.wait_exit(d)
check("gone-qemu-returns-at-once", time.time() - t < 3, time.time() - t)
subprocess.run(["rm", "-rf", T])
sys.exit(1 if fails else 0)
