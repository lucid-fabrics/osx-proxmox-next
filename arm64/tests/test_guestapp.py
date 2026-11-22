"""lib/guestapp.py (R29, S9 bugs 2 and 3): the detached open, its bounded retry, the window and WebContent checks. No guest: `run` is a stub."""
import importlib.util, os, subprocess, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("guestapp", HERE + "/lib/guestapp.py")
ga = importlib.util.module_from_spec(spec); spec.loader.exec_module(ga)
fails = []


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        fails.append(name)


class Guest:
    """a stub for subprocess.run: script(cmd) returns (rc, stdout) or raises TimeoutExpired; every command is kept"""
    def __init__(self, script):
        self.script, self.cmds = script, []

    def __call__(self, argv, capture_output=True, text=True, timeout=None):
        cmd = argv[-1]; self.cmds.append(cmd)
        r = self.script(cmd, len(self.cmds))
        if r == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, r[0], r[1], "")


class Time:
    """a clock that only moves when sleep is called"""
    def __init__(self): self.t = 1000.0
    def clock(self): return self.t
    def sleep(self, s): self.t += s


SSH = ["ssh", "host"]

# the open is detached: the remote shell backgrounds it, so ssh returns at once whatever `open` does
c = ga.detached_open("Safari", "http://10.0.2.2:8000/a.html?x=1&y=2")
check("detached-open-is-backgrounded", c.startswith("perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' ") and c.endswith(">/dev/null 2>&1 </dev/null &"), c)
check("detached-open-has-its-own-session", "setsid" in c)
check("detached-open-runs-in-the-user-session", "launchctl asuser 501 open -a Safari" in c)
import shlex
url = "http://x/'; touch /tmp/PWN #"
parts = shlex.split(ga.detached_open("Safari", url).rsplit(" >/dev/null", 1)[0])
check("detached-open-quotes-the-url", parts[-1] == url and parts[-2] == "Safari", parts)
check("detached-open-no-url", ga.detached_open("Notes").endswith("open -a Notes >/dev/null 2>&1 </dev/null &"), ga.detached_open("Notes"))

# open_app: process shows up at once
t = Time(); g = Guest(lambda cmd, n: (0, "up\n") if "pgrep" in cmd else (0, ""))
r = ga.open_app(SSH, "Safari", "http://x/", run=g, clock=t.clock, sleep=t.sleep)
check("open-ok-at-once", r["ok"] and r["tries"] == 1 and len(g.cmds) == 2, (r, g.cmds))

# the ssh that carries the open fails to connect (255) twice, then works: retried, bounded
t = Time(); seen = {"n": 0}
def flaky(cmd, n):
    if "setsid" in cmd:
        seen["n"] += 1
        return (255, "") if seen["n"] <= 2 else (0, "")
    return (0, "up")
g = Guest(flaky)
r = ga.open_app(SSH, "Safari", run=g, clock=t.clock, sleep=t.sleep)
check("open-retries-ssh-255", r["ok"] and seen["n"] == 3, (r, seen))
t = Time(); g = Guest(lambda cmd, n: (255, ""))
r = ga.open_app(SSH, "Safari", verify_s=10, tries=2, run=g, clock=t.clock, sleep=t.sleep)
check("open-255-forever-is-not-ok", not r["ok"] and r["tries"] == 2 and len(g.cmds) < 60 and t.t - 1000.0 < 200, (r, len(g.cmds), t.t - 1000.0))

# an ssh that hangs (the 90 s of S9 bug 3) is cut at per_call; the process check decides
t = Time()
g = Guest(lambda cmd, n: "timeout" if "setsid" in cmd else (0, "up"))
r = ga.open_app(SSH, "Safari", per_call=20, run=g, clock=t.clock, sleep=t.sleep)
check("open-ssh-hang-does-not-matter-when-the-process-is-up", r["ok"], r)

# the process never appears: asked again `tries` times, then not ok, and the whole thing is bounded
t = Time(); g = Guest(lambda cmd, n: (0, ""))
r = ga.open_app(SSH, "Safari", verify_s=30, tries=3, poll=3, run=g, clock=t.clock, sleep=t.sleep)
opens = [c for c in g.cmds if "setsid" in c]
check("open-asks-again-then-gives-up", not r["ok"] and len(opens) == 3 and r["tries"] == 3, (r, len(opens)))
check("open-bounded-in-time", t.t - 1000.0 <= 3 * (30 + 20 + 3) + 5, t.t - 1000.0)
logs = []
t = Time(); g = Guest(lambda cmd, n: (0, ""))
ga.open_app(SSH, "Safari", verify_s=5, tries=2, run=g, clock=t.clock, sleep=t.sleep, log=logs.append)
check("open-logs-what-it-did", any("giving up" in l for l in logs) and any("asking again" in l for l in logs), logs)

# windows and ready
def guest(win, age=None, osa_rc=0, gpu=None):
    def f(cmd, n):
        if "osascript" in cmd:
            return (osa_rc, "" if win is None else "%s\n" % win)
        if "ps -axo" in cmd and "WebKit.GPU" in cmd:
            return (0, "" if gpu is None else "%s /x/com.apple.WebKit.GPU\n" % gpu)
        if "ps -axo" in cmd:
            return (0, "" if age is None else "%s /x/com.apple.WebKit.WebContent\n" % age)
        return (0, "")
    return Guest(f)


check("windows-counted", ga.windows_on_screen(SSH, "Safari", run=guest(2)) == 2)
check("windows-zero", ga.windows_on_screen(SSH, "Safari", run=guest(0)) == 0)
check("windows-unknown-on-failure", ga.windows_on_screen(SSH, "Safari", run=guest(None, osa_rc=1)) is None and ga.windows_on_screen(SSH, "Safari", run=guest(None)) is None)
g = guest(1)
ga.windows_on_screen(SSH, "Safari", run=g)
check("windows-asks-the-window-server-as-the-user", "launchctl asuser 501 osascript -l JavaScript" in g.cmds[0] and "CGWindowListCopyWindowInfo" in g.cmds[0] and '"Safari"' in g.cmds[0], g.cmds[0])
check("windows-quotes-the-app-name", '"Evil\\"; id"' in ga._jxa('Evil"; id', 200))
for s_, want in (("00:07", 7), ("01:30", 90), ("1:02:03", 3723), ("2-00:00:01", 172801), ("x", None), ("", None)):
    check("etime-%s" % (s_ or "empty"), ga.etime_seconds(s_) == want, ga.etime_seconds(s_))
check("youngest-age", ga.youngest_age(SSH, "WebContent", run=Guest(lambda c, n: (0, "05:00 a\n00:20 b\n12:00 c\n"))) == 20)
check("youngest-none", ga.youngest_age(SSH, "WebContent", run=Guest(lambda c, n: (1, ""))) is None)
check("ready-window-and-stable-webcontent", ga.ready(SSH, "Safari", run=guest(1, "01:30"))[0] == "ready")
check("ready-no-window", ga.ready(SSH, "Safari", run=guest(0, "01:30"))[0] == "not-ready")
check("ready-webcontent-restarting", ga.ready(SSH, "Safari", run=guest(1, "00:03"))[0] == "not-ready" and "3 s old" in ga.ready(SSH, "Safari", run=guest(1, "00:03"))[1])
check("ready-gpu-process-being-killed", ga.ready(SSH, "Safari", run=guest(1, "01:30", gpu="00:04"))[0] == "not-ready" and "GPU process" in ga.ready(SSH, "Safari", run=guest(1, "01:30", gpu="00:04"))[1])
check("ready-old-gpu-process-ok", ga.ready(SSH, "Safari", run=guest(1, "01:30", gpu="02:00"))[0] == "ready")
check("ready-window-without-page-process", ga.ready(SSH, "Safari", run=guest(1, None))[0] == "not-ready")
check("ready-unknown-when-osascript-fails", ga.ready(SSH, "Safari", run=guest(None, osa_rc=1))[0] == "unknown")
check("ready-other-app-needs-only-a-window", ga.ready(SSH, "Notes", run=guest(1, None))[0] == "ready")

# OpenJob has the poll() of a Popen
job = ga.OpenJob(SSH, "Safari", "http://x/", run=Guest(lambda c, n: (0, "up")), poll=0.01)
check("openjob-finishes-ok", job.wait(5) == 0 and job.poll() == 0 and job.result["ok"])
t = Time()
job = ga.OpenJob(SSH, "Safari", "http://x/", verify_s=1, tries=1, run=Guest(lambda c, n: (0, "")), clock=t.clock, sleep=t.sleep)
check("openjob-reports-failure", job.wait(5) == 1)

sys.exit(1 if fails else 0)
