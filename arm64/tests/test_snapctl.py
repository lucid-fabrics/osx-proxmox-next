"""snapctl: name rule, atomic save (a failed save keeps the old snapshot), quoted exec: migration URIs, no regex in the writer wait."""
import importlib.util, json, os, shlex, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="snc-")
os.environ["SNAPS"] = T + "/snaps"
os.environ["SNAPCTL_QMP_TIMEOUT"] = "1"
os.makedirs(T + "/snaps")
spec = importlib.util.spec_from_file_location("snapctl", HERE + "/lib/snapctl.py")
sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
fails = []


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        fails.append(name)


def hidden():
    return [n for n in os.listdir(T + "/snaps") if n.startswith(".")]


# names
for n in ("a", "bench-20261120-101010", "x.y_z-1", "A" * 64, "0"):
    check("name-ok-%s" % n[:12], sc.valid_name(n))
for n in ("", ".", "..", ".a", "-a", "a/b", "a b", "a;b", "a..b", "a\n", "x" * 65, "a$(x)", "é", None):
    check("name-bad-%r" % (n,), not sc.valid_name(n))
for cmd in (["finish", "..", T, "q", "1", "1G", "", "0"], ["check", "../x", "q", "1", "1G", "", "0"], ["age", ".."], ["incoming", ".a", T]):
    r = subprocess.run([sys.executable, "-I", HERE + "/lib/snapctl.py"] + cmd, capture_output=True, text=True, env=dict(os.environ))
    check("cli-rejects-%s" % cmd[0], r.returncode == 2 and "invalid snapshot name" in r.stderr, r.returncode)

# atomic save
qemu = T + "/qemu"; open(qemu, "w").write("fake qemu")


def old_snapshot():
    d = T + "/snaps/s1"
    os.makedirs(d, exist_ok=True)
    json.dump({"old": True}, open(d + "/meta.json", "w")); open(d + "/vm.state", "w").write("old")


def stage(rundir, complete):
    d = sc.stage_dir("s1", rundir)
    os.makedirs(d, exist_ok=True)
    open(d + "/vm.state", "w").write("new")
    json.dump({"fmt": "zstd"}, open(d + "/fmt.json", "w"))
    if complete:
        json.dump({"host_wall": 1.0, "guest_clock": "1"}, open(d + "/saved.json", "w"))
    return d


run1 = T + "/runs/run-AAAAAA"; os.makedirs(run1)
open(run1 + "/aux", "w").write("a"); open(run1 + "/root.qcow2", "w").write("r")
old_snapshot(); stage(run1, complete=False)
rc = sc.finish("s1", run1, qemu, 2, "1G", "", 0)
check("failed-save-keeps-old", rc == 1 and open(T + "/snaps/s1/vm.state").read() == "old" and json.load(open(T + "/snaps/s1/meta.json")) == {"old": True})
check("failed-save-drops-stage", not hidden(), hidden())
stage(run1, complete=True)
rc = sc.finish("s1", run1, qemu, 2, "1G", "", 0)
m = json.load(open(T + "/snaps/s1/meta.json"))
check("good-save-replaces-old", rc == 0 and open(T + "/snaps/s1/vm.state").read() == "new" and "binary_md5" in m and "old" not in m)
check("good-save-leaves-no-hidden-dirs", not hidden(), hidden())
check("good-save-files", all(os.path.isfile(T + "/snaps/s1/" + n) for n in ("aux", "root.qcow2", "meta.json", "vm.state")))
old_snapshot(); stage(run1, complete=True)
sc.abort("s1", run1)
check("abort-drops-stage-keeps-old", open(T + "/snaps/s1/vm.state").read() == "old" and not hidden())
# save() with no QEMU to talk to (the QMP socket never appears): fails, the stage is gone and the old snapshot stays
run2 = T + "/runs/run-BBBBBB"; os.makedirs(run2)
try:
    rc = sc.save("s1", run2, qemu, 2, "1G", "", 0, 10022, T + "/nokey", "0")
except Exception:
    rc = 1
check("save-without-qemu-fails-cleanly", rc != 0 and open(T + "/snaps/s1/vm.state").read() == "old" and not hidden(), hidden())

# quoted exec: URIs: run them through sh the way QEMU does, with a fake zstd
bindir = T + "/bin"; os.makedirs(bindir)
open(bindir + "/zstd", "w").write('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$ARGV_OUT"\n'); os.chmod(bindir + "/zstd", 0o755)
for i, state in enumerate((T + "/with space/vm.state", T + "/semi;touch PWNED1/vm.state", T + "/$(touch PWNED2)/vm.state",
                           T + "/q'uo\"te/vm.state", T + "/a+b(c)[d].state")):
    for kind, uri, want in (("write", sc.zstd_write_uri(state), ["-T0", "-1", "-q", "-f", "-o", state]),
                            ("read", sc.zstd_read_uri(state), ["-d", "-T0", "-q", "-c", state])):
        out = T + "/argv"
        subprocess.run(["sh", "-c", uri[len("exec:"):]], env=dict(os.environ, PATH=bindir + ":" + os.environ["PATH"], ARGV_OUT=out), cwd=T)
        got = open(out).read().split("\n")[:-1]
        check("exec-uri-%s-%d" % (kind, i), uri.startswith("exec:") and got == want, (got, want))
check("exec-uri-no-injection", not any(n.startswith("PWNED") for n in os.listdir(T)))

# the writer wait looks at /proc: a path with regex characters is no problem
state = T + "/a+b(c)[d].state"
p = subprocess.Popen(["sh", "-c", "sleep 5 # zstd -o " + shlex.quote(state)])
check("writer-running", sc.writer_running(state))
p.kill(); p.wait()
check("writer-gone", not sc.writer_running(state))

# guest clock sampling. A slow round trip says nothing about a 0.5 s limit: it is refused, sampling goes on until the guest is
# quick, and the answer is the quickest of the good ones. The reader is one long-lived process, not one spawn per sample.
def fake(seq):
    it = iter(seq)
    return lambda: next(it, None)


far = time.time() + 30
check("measure-refuses-slow", sc.measure(fake([(1.7, -1.08), (2.2, 0.9)]), 0.25, time.time() + 0.6, pause=0.01) is None)
check("measure-waits-for-quiet", sc.measure(fake([(1.7, -1.08), (0.9, 0.5), (0.2, 0.1), (0.1, 0.07), (0.15, 0.06)]), 0.25, far, pause=0.01) == (0.1, 0.07))
check("measure-takes-the-shortest", sc.measure(fake([(0.2, 0.3), (0.05, 0.1), (0.1, 0.2)]), 0.25, far, pause=0.01) == (0.05, 0.1))
check("measure-none-reading-skipped", sc.measure(fake([None, (0.1, 0.0), None, (0.1, 0.0), (0.2, 0.0)]), 0.25, far, pause=0.01) == (0.1, 0.0))
check("measure-respects-deadline", sc.measure(fake([]), 0.25, time.time() + 0.3, pause=0.05) is None)
logged = []
sc.measure(fake([(0.3, 0.0), (0.1, 0.0), (0.1, 0.0), (0.1, 0.0)]), 0.25, far, pause=0.01, log=logged.append)
check("measure-logs-every-sample", len(logged) == 4, logged)
check("within-counts-half-rtt", sc.within((0.2, 0.39), 0.5) and not sc.within((0.2, 0.41), 0.5) and sc.within((0.0, -0.5), 0.5))

gc = sc.GuestClock(["sh", "-c"])
a = gc.sample(10); b = gc.sample(10)
check("guestclock-two-samples", a is not None and b is not None and a[0] < 5 and abs(a[1]) < 1 and abs(b[1]) < 1, (a, b))
pid = gc.p.pid
for _ in range(3):
    gc.sample(10)
check("guestclock-one-process", gc.p.pid == pid and gc.p.poll() is None)
gc.close()
check("guestclock-closed", gc.p.poll() is not None and gc.sample(1) is None)
# a guest that starts answering late: the first sample times out, the next one works and is never fed the old answer
open(T + "/late.sh", "w").write('sleep 0.8\nsh -c "$1"\n')
late = sc.GuestClock(["sh", T + "/late.sh"])
check("guestclock-timeout", late.sample(0.2) is None)
d = late.sample(10)
check("guestclock-recovers-after-timeout", d is not None and abs(d[1]) < 1, d)
late.close()


class FakeClock:
    """a guest whose clock is off by .off; slow: round trips of that many seconds until quick_after samples were taken"""
    class P:
        dead = False
        def poll(self): return 1 if self.dead else None

    def __init__(self, off, slow=0, rtt_slow=2.0, step_ok=True, step_fixes=True):
        self.off, self.n, self.slow, self.rtt_slow, self.step_ok, self.step_fixes = off, 0, slow, rtt_slow, step_ok, step_fixes
        self.steps = 0; self.closed = False; self.p = FakeClock.P()

    def sample(self, timeout=10.0):
        self.n += 1
        if self.n <= self.slow:
            return (self.rtt_slow, self.off + self.rtt_slow / 2)       # the spawn bias: reads half the round trip too high
        return (0.01, self.off)

    def step(self, host_time, timeout=10.0):
        self.steps += 1
        if self.step_ok and self.step_fixes:
            self.off = 0.002
        return self.step_ok

    def close(self): self.closed = True


def settle(clock, limit=0.5, secs=5, fallback=None):
    r = {}
    ok = sc.settle_clock(lambda: clock, r, limit, time.time() + secs, lambda b: None, fallback=fallback)
    return ok, r


# the late-clock case: the clock is right (-0.005 s) while slow round trips read it +1.2 s off. Nothing is stepped, the answer is OK.
c = FakeClock(-0.005, slow=3, rtt_slow=2.4)
ok, r = settle(c)
check("settle-slow-readings-do-not-step", ok and c.steps == 0 and r["method"] == "none" and r["attempts"] == 0 and abs(r["diff_s"]) < 0.1, (ok, r, c.steps))
c = FakeClock(-12.0)
ok, r = settle(c)
check("settle-steps-a-guest-that-is-off", ok and c.steps == 1 and r["method"] == "settimeofday" and abs(r["diff_s"]) < 0.5, (ok, r))
c = FakeClock(-12.0, step_fixes=False)
ok, r = settle(c, secs=2)
check("settle-gives-up-at-the-budget", not ok and c.steps == 3 and r["attempts"] == 3, (ok, r, c.steps))
c = FakeClock(-12.0, step_ok=False)
used = []
ok, r = settle(c, secs=60, fallback=lambda: (used.append(1), setattr(c, "off", 0.0)))
check("settle-date-fallback", ok and used == [1] and r["method"] == "date", (ok, r, used))
c = FakeClock(0.0, slow=10 ** 6)
ok, r = settle(c, secs=1)
check("settle-never-trusts-a-slow-guest", not ok and c.steps == 0 and "diff_s" not in r, (ok, r))
check("settle-closes-the-clock", c.closed)
dead = FakeClock(0.0); dead.p.dead = True; made = []
def make():
    made.append(1)
    if len(made) == 1:
        return dead
    return FakeClock(0.1)
ok = sc.settle_clock(make, {}, 0.5, time.time() + 5, lambda b: None)
check("settle-restarts-a-dead-session", ok and len(made) == 2, made)

# what a SIGKILLed save leaves in SNAPS goes with the next command; a snapshot a kill left in `.old-*` comes back
check("leftover-parse-stage", sc.parse_leftover(".stage-soak-soak-90bxpB") == ("stage", "soak", "soak-90bxpB"))
check("leftover-parse-old-dashes", sc.parse_leftover(".old-a-b-a-b-Ab12Cd") == ("old", "a-b", "a-b-Ab12Cd"))
check("leftover-parse-rejects", all(sc.parse_leftover(x) is None for x in (".stage-soak", ".stage-soak-other-90bxpB", ".stage--x", ".snapshot", ".old-a-b-123456", "soak")))
RR = T + "/runs2"; os.makedirs(RR)
os.makedirs(T + "/snaps/keep"); open(T + "/snaps/keep/vm.state", "w").write("kept")


def leftover(entry, age=0, data="x"):
    d = T + "/snaps/" + entry
    os.makedirs(d, exist_ok=True); open(d + "/vm.state", "w").write(data)
    old_t = time.time() - age
    os.utime(d + "/vm.state", (old_t, old_t)); os.utime(d, (old_t, old_t))
    return d


a = leftover(".stage-keep-keep-AAAAAA", age=5)      # of a run named as dead
b = leftover(".stage-keep-keep-BBBBBB", age=5)      # no run dir, written a moment ago: another snapintosh may own it
c = leftover(".stage-keep-keep-CCCCCC", age=600)    # no run dir, quiet for 10 minutes: an old leak
os.makedirs(RR + "/keep-DDDDDD"); d = leftover(".stage-keep-keep-DDDDDD", age=600)   # its run dir exists: never touched
sc.reap(RR, "1", "keep-AAAAAA")
check("reap-removes-the-stage-of-a-dead-run", not os.path.exists(a))
check("reap-leaves-a-fresh-orphan", os.path.exists(b))
c0 = leftover(".stage-keep-keep-GGGGGG", age=600); sc.reap(RR, "0")
check("reap-shared-snaps-never-takes-orphans", os.path.exists(c0)); shutil0 = __import__("shutil"); shutil0.rmtree(c0)
check("reap-removes-an-old-orphan", not os.path.exists(c))
check("reap-leaves-a-stage-with-a-run-dir", os.path.exists(d))
check("reap-never-touches-the-snapshot", open(T + "/snaps/keep/vm.state").read() == "kept")
shutil = __import__("shutil"); shutil.rmtree(b); shutil.rmtree(d)
leftover(".old-keep-keep-EEEEEE", data="the old one")
sc.reap(RR, "1", "keep-EEEEEE")
check("reap-drops-old-when-the-snapshot-is-there", not os.path.exists(T + "/snaps/.old-keep-keep-EEEEEE") and open(T + "/snaps/keep/vm.state").read() == "kept")
shutil.rmtree(T + "/snaps/keep"); leftover(".old-keep-keep-FFFFFF", data="the old one")
sc.reap(RR, "1", "keep-FFFFFF")
check("reap-puts-back-a-snapshot-the-kill-set-aside", open(T + "/snaps/keep/vm.state").read() == "the old one" and not os.path.exists(T + "/snaps/.old-keep-keep-FFFFFF"))
r = subprocess.run([sys.executable, "-I", HERE + "/lib/snapctl.py", "reap", RR, "1"], capture_output=True, text=True, env=dict(os.environ))
check("reap-cli-no-args-ok", r.returncode == 0, r.stderr)
check("reap-quiet-when-nothing", r.stdout == "", r.stdout)

# the banner probe tells the guest's sshd saying hello from the login behind it
import socket, threading
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(2)
threading.Thread(target=lambda: [(lambda c: (c.sendall(b"SSH-2.0-x\r\n"), c.close()))(srv.accept()[0]) for _ in range(2)], daemon=True).start()
out, stop = {}, threading.Event()
sc.banner_probe(srv.getsockname()[1], time.time() - 3.0, out, stop)
check("banner-probe-records-seconds-after-cont", 2.9 <= out.get("banner_s", 0) <= 4.5, out)
check("banner-note-splits-the-wait", "said hello after 3.0 s" in sc.banner_note({"ssh_s": 57.0, "ssh_banner_s": 3.0}) and "54.0 s more" in sc.banner_note({"ssh_s": 57.0, "ssh_banner_s": 3.0}))
check("banner-note-silent-without-banner", sc.banner_note({"ssh_s": 5.0}) == "" and sc.banner_note({"ssh_s": 5.0, "ssh_banner_s": 5.0}) == "")
stop.set(); srv.close()
closed = socket.socket(); closed.bind(("127.0.0.1", 0)); port = closed.getsockname()[1]; closed.close()
st = threading.Event(); threading.Timer(0.5, st.set).start()
o2 = {}
sc.banner_probe(port, time.time(), o2, st)
check("banner-probe-gives-up-when-told-and-nothing-listens", o2 == {})

# the app gate. A stub guestapp answers ready / not-ready / unknown.
class StubGA:
    def __init__(self, states, running=True): self.states, self.running, self.n = list(states), running, 0
    def is_running(self, ssh, app): return self.running
    def ready(self, ssh, app):
        self.n += 1
        return self.states[min(self.n - 1, len(self.states) - 1)]


def gate(ga, required, appwait="1", stable="0"):
    sc.guestapp = lambda: ga
    os.environ["APPWAIT"] = appwait; os.environ["APPSTABLE"] = stable
    try:
        return sc.app_gate(["ssh"], "Safari", required)
    finally:
        os.environ.pop("APPWAIT", None); os.environ.pop("APPSTABLE", None)


check("gate-ready-passes", gate(StubGA([("ready", "1 window(s) on screen")]), True) == 0)
check("gate-not-ready-required-refuses", gate(StubGA([("not-ready", "no Safari window on screen")]), True) == sc.APP_REFUSED == 5)
check("gate-not-ready-unnamed-only-warns", gate(StubGA([("not-ready", "x")]), False) == 0)
check("gate-unknown-never-blocks", gate(StubGA([("unknown", "could not ask")]), True) == 0)
# an answer earlier in the window counts; only a window with no answer at all passes as "unknown"
check("gate-late-unknown-after-not-ready-refuses", gate(StubGA([("not-ready", "x"), ("unknown", "timed out")]), True, appwait="6") == sc.APP_REFUSED)
check("gate-unnamed-app-not-running-skips", gate(StubGA([("not-ready", "x")], running=False), False) == 0)
check("gate-off-with-appwait-0", gate(StubGA([("not-ready", "x")]), True, appwait="0") == 0)
check("gate-needs-ready-to-hold", gate(StubGA([("ready", "a"), ("not-ready", "restarted"), ("ready", "b"), ("ready", "c"), ("ready", "d"), ("ready", "e")]), True, appwait="60", stable="4") == 0 and True)
st = StubGA([("ready", "a"), ("not-ready", "restarted")] * 40)
check("gate-flapping-never-passes", gate(st, True, appwait="12", stable="8") == sc.APP_REFUSED)
check("gate-waits-until-it-shows", gate(StubGA([("not-ready", "x"), ("ready", "ok")]), True, appwait="30") == 0)
# WebKit's error page is told from a page by the picture; the gate then opens the page again
def flat(w, h, rgb, button=False):
    px = bytearray(bytes(rgb) * (w * h))
    if button:
        for y in range(int(h * 0.51), int(h * 0.555)):
            for x in range(int(w * 0.45), int(w * 0.55)):
                o = (y * w + x) * 3; px[o:o + 3] = bytes((0, 122, 255))
    return w, h, bytes(px)


check("crash-page-grey-with-reload-button", sc.crash_page(*flat(720, 540, (246, 246, 246), button=True)))
check("crash-page-scale-free", sc.crash_page(*flat(1440, 1080, (246, 246, 246), button=True)))
check("crash-page-not-a-white-page", not sc.crash_page(*flat(720, 540, (255, 255, 255))))
check("crash-page-not-grey-without-button", not sc.crash_page(*flat(720, 540, (246, 246, 246))))
check("crash-page-not-the-desktop", not sc.crash_page(*flat(720, 540, (200, 120, 60), button=True)))
check("crash-page-short-buffer-is-no", not sc.crash_page(720, 540, b"x"))
pics = [flat(720, 540, (246, 246, 246), button=True), flat(720, 540, (0, 255, 204))]
reopened = []
real_dump = sc.screen_dump
sc.screen_dump = lambda q, path: pics.pop(0) if len(pics) > 1 else pics[0]
os.makedirs(T + "/rd", exist_ok=True)
sc.guestapp = lambda: StubGA([("ready", "w")])
os.environ["APPWAIT"] = "30"; os.environ["APPSTABLE"] = "0"
g = sc.app_gate(["ssh"], "Safari", True, object(), T + "/rd", lambda: reopened.append(1))
os.environ.pop("APPWAIT"); os.environ.pop("APPSTABLE"); sc.screen_dump = real_dump
check("gate-error-page-opens-the-page-again-then-passes", g == 0 and reopened == [1], (g, reopened))

os.environ.pop("SAVE_APP", None); os.environ.pop("SAVE_OPEN_URL", None)
check("options-default-auto-safari", sc.save_app_options() == ("Safari", False, ""))
os.environ["SAVE_APP"] = "Notes"
check("options-app-required", sc.save_app_options() == ("Notes", True, ""))
os.environ["SAVE_APP"] = ""; os.environ["SAVE_OPEN_URL"] = "http://x/"
check("options-open-implies-safari-required", sc.save_app_options() == ("Safari", True, "http://x/"))
os.environ.pop("SAVE_OPEN_URL")

subprocess.run(["rm", "-rf", T])
sys.exit(1 if fails else 0)
