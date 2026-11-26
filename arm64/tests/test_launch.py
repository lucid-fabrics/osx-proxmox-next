"""Whole-CLI tests of run/snapshot/resume with a fake QEMU, qemu-nbd and qemu-img (no Apple files, no real VM).

Closes (signals end the CLI and everything it started, interrupted saves exit 128+sig, pid files never name a victim, `&` in the home, socket path cap): exit codes follow QEMU and the helpers (4, 6), one cleanup trap covers every path (8), a failing helper kills an idle QEMU,
run dirs are unique (14), umask 077 (9), VNC password by default (10), snapshot names validated (5), a failed save keeps the old one."""
import glob, json, os, re, shutil, signal, stat, subprocess, sys, tempfile, time

# A job started with `&` from a non-interactive shell (a CI step, a tool's background run) has SIGINT ignored, and every child inherits
# that: the CLI would then ignore the Ctrl-C these tests send it. Resetting it here makes the tests the same in the foreground and in
# the background. (The CLI itself still ignores SIGINT when started that way, which is how shells treat background jobs.)
signal.signal(signal.SIGINT, signal.SIG_DFL)
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(HERE, "snapintosh")
T = tempfile.mkdtemp(prefix="snl-")
HOME, PREFIX = T + "/home", T + "/prefix"
TOKEN = "snl%d" % os.getpid()
fails = []


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)
    os.chmod(path, mode)


# ---- the fake prefix and a golden
write(PREFIX + "/bin/qemu-system-aarch64", """#!/bin/bash
[ -z "${FAKE_ARGS:-}" ] || printf '%s\\n' "$@" > "$FAKE_ARGS"
[ -z "${FAKE_ENV:-}" ] || env > "$FAKE_ENV"
[ -z "${FAKE_PCLINE:-}" ] || echo "qemu-system-aarch64: info: pcache: $FAKE_PCLINE, rw, boot 1, 5 records, 1 MiB, opened"
case "${FAKE_QEMU:-exit:0}" in
  exit:*) exit "${FAKE_QEMU#exit:}" ;;
  sleep) exec -a "$FAKE_TOKEN" sleep 300 ;;
  qmp) exec python3 "$FAKE_QMP" "$@" ;;
esac
""", 0o755)
write(PREFIX + "/bin/qemu-nbd", """#!/bin/bash
# like the real one: a daemon whose command line names qemu-nbd and its socket, pid in the --pid-file
prev=""
for a in "$@"; do case $a in --pid-file=*) pf=${a#--pid-file=} ;; esac; [ "$prev" = -k ] && sock=$a; prev=$a; done
( exec -a "qemu-nbd -k $sock ${FAKE_TOKEN}-nbd" sleep 300 ) </dev/null >/dev/null 2>&1 &
echo $! > "$pf"
""", 0o755)
write(PREFIX + "/bin/qemu-img", """#!/bin/bash
for a in "$@"; do last=$a; done
: > "$last"
""", 0o755)
# a QEMU that serves QMP like the real one for the commands the helpers send. FAKE_MIGRATE: ok (writes the state file), fail, hang (never ends);
# FAKE_INCOMING: ok or fail. `quit` ends it with status 0.
write(T + "/fakeqmp.py", """
import json, os, shlex, socket, sys
a = sys.argv[1:]
path = [a[i + 1] for i, x in enumerate(a) if x == "-qmp"][0].split(":", 1)[1].split(",")[0]
srv = socket.socket(socket.AF_UNIX); srv.bind(path); srv.listen(1)
while True:
    c, _ = srv.accept(); f = c.makefile("rw")
    f.write(json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\\n"); f.flush()
    mig = "none"
    for line in f:
        d = json.loads(line); n = d["execute"]; arg = d.get("arguments", {}); r = {}
        if n == "migrate":
            uri = arg["uri"]; st = uri[5:] if uri.startswith("file:") else shlex.split(uri[5:])[-1]
            m = os.environ.get("FAKE_MIGRATE", "ok")
            if m == "ok": open(st, "w").write("state"); mig = "completed"
            else: mig = "failed" if m == "fail" else "active"
        elif n == "migrate-incoming": mig = "completed" if os.environ.get("FAKE_INCOMING", "ok") == "ok" else "failed"
        elif n == "query-migrate": r = {"status": mig, "error-desc": "fake failure", "ram": {"total": 0}}
        elif n == "quit":
            f.write(json.dumps({"return": {}}) + "\\n"); f.flush(); os._exit(0)
        f.write(json.dumps({"return": r}) + "\\n"); f.flush()
""")
# an ssh that answers (FAKE_SSH=ok) or refuses the connection (fail)
write(T + "/bin/ssh", """#!/bin/bash
[ "${FAKE_SSH:-ok}" = ok ] || exit 255
for a in "$@"; do last=$a; done
[ -z "${FAKE_SSHLOG:-}" ] || printf '%s\\n' "$last" >> "$FAKE_SSHLOG"
[ "$last" != "date +%s" ] || date +%s
# an app in the guest. FAKE_APP: ready (a window and an old WebContent), young (a window, WebContent 3 s old), nowin (running, no window)
case $last in
  *"pgrep -x Dock"*) echo desk ;;
  *"pgrep -x Safari"*) [ -z "${FAKE_APP:-}" ] || echo up ;;
  *osascript*) case ${FAKE_APP:-} in ready|young) echo 1 ;; nowin) echo 0 ;; esac ;;
  *"ps -axo"*) case ${FAKE_APP:-} in ready) echo "01:30 /x/com.apple.WebKit.WebContent" ;; young) echo "00:03 /x/com.apple.WebKit.WebContent" ;; esac ;;
esac
exit 0
""", 0o755)
write(PREFIX + "/share/snapintosh/ambrosia/ambrosia.bin", "x")
write(PREFIX + "/share/snapintosh/ambrosia/config.cpio", "x")
write(HOME + "/vm/aux.img", "aux")
write(HOME + "/vm/golden/root-sshd.qcow2", "golden")
os.chmod(HOME, 0o755)

BASE = dict(os.environ, SNAPINTOSH_HOME=HOME, SNAPINTOSH_PREFIX=PREFIX, XDG_CONFIG_HOME=T + "/cfg", SNAPCTL_QMP_TIMEOUT="2",
            FAKE_TOKEN=TOKEN, PCCOMPACT="0", FAKE_QMP=T + "/fakeqmp.py", PATH=T + "/bin:" + os.environ["PATH"], SAVE_SSH_FIRST="0",
            WALLWAIT="0", SAVESTEP="0", SNAPFMT="zstd", SNAPINTOSH_NO_SETTIME="1")
for k in ("SNAPS", "SNAPINTOSH_DRY", "KEEP_RUNS"):
    BASE.pop(k, None)
RUNS = HOME + "/runs"
OPTS = ["--gpu", "0", "--cpus", "2", "--ram", "64M", "--secs", "40"]


def cli(args, env=None, timeout=60, umask=None):
    e = dict(BASE, **(env or {}))
    pre = (lambda: os.umask(umask)) if umask is not None else None
    return subprocess.run([CLI] + args, env=e, capture_output=True, text=True, timeout=timeout, preexec_fn=pre)


def procs(needle):
    out = []
    for p in os.listdir("/proc"):
        if p.isdigit() and int(p) != os.getpid():
            try:
                if needle.encode() in open("/proc/%s/cmdline" % p, "rb").read():
                    out.append(int(p))
            except OSError:
                pass
    return out


def leftovers(settle=4.0):
    end = time.time() + settle
    while True:
        left = procs(TOKEN) + procs(T + "/home/runs") + procs(T + "/bin/ssh")
        if not left or time.time() > end:
            return left
        time.sleep(0.2)


def rundirs():
    return sorted(glob.glob(RUNS + "/*/"))


def reset():
    for p in procs(TOKEN) + procs(T + "/home/runs") + procs(T + "/bin/ssh"):
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    subprocess.run(["rm", "-rf", RUNS, HOME + "/snaps"])


def check(name, cond, detail=""):
    if cond:
        print("pass  " + name)
    else:
        print("FAIL  %s %s" % (name, detail))
        fails.append(name)


# 1. exit codes
reset()
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
check("run-ok-exit-0", r.returncode == 0 and not rundirs() and not leftovers(), r.stdout[-300:] + r.stderr[-300:])
reset()
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:7"})
check("run-qemu-fails-exit-nonzero", r.returncode != 0 and "status 7" in r.stderr, "rc=%s %s" % (r.returncode, r.stderr[-300:]))
check("run-qemu-fails-dir-kept", len(rundirs()) == 1)
check("run-qemu-fails-no-leftovers", not leftovers(), str(procs(TOKEN)))

# --secs reached: the documented way to stop a run, not a failure
reset()
r = cli(["run", "--gpu", "0", "--cpus", "2", "--ram", "64M", "--secs", "1"], {"FAKE_QEMU": "sleep"})
check("secs-limit-is-clean", r.returncode == 0 and not rundirs() and not leftovers(), "rc=%s %s" % (r.returncode, r.stderr[-200:]))

# 2. umask and modes (the caller's umask is 022 here)
reset()
r = cli(["run", "--vnc"] + OPTS, {"FAKE_QEMU": "exit:1"}, umask=0o022)
rd = rundirs()
bad = subprocess.run(["find", HOME + "/runs", "-perm", "/077"], capture_output=True, text=True).stdout.split() if rd else ["no run dir"]
check("home-mode-700", stat.S_IMODE(os.stat(HOME).st_mode) == 0o700)
check("run-files-owner-only", not bad, str(bad[:3]))

# 3. VNC: a random password in a 0600 file by default, none with --vnc-insecure, never printed
reset()
args = T + "/args"
r = cli(["run", "--vnc"] + OPTS, {"FAKE_QEMU": "exit:1", "FAKE_ARGS": args})
a = open(args).read()
pw_files = glob.glob(RUNS + "/*/vnc.pass")
pw = open(pw_files[0]).read().strip() if pw_files else ""
check("vnc-password-file", len(pw_files) == 1 and len(pw) == 8 and stat.S_IMODE(os.stat(pw_files[0]).st_mode) == 0o600)
check("vnc-secret-on-qemu", "password-secret=vncsec" in a and "secret,id=vncsec,file=" in a)
check("vnc-password-not-printed", pw and pw not in r.stdout + r.stderr and "vnc.pass" in r.stdout)
reset()
r = cli(["run", "--vnc", "--vnc-insecure"] + OPTS, {"FAKE_QEMU": "exit:1", "FAKE_ARGS": args})
check("vnc-insecure-opt-out", "password-secret" not in open(args).read() and not glob.glob(RUNS + "/*/vnc.pass"))
reset()
cli(["run"] + OPTS, {"FAKE_QEMU": "exit:1", "FAKE_ARGS": args})
check("no-vnc-by-default", "-vnc" not in open(args).read().split("\n"))

# 4. cleanup on a die before QEMU starts (the old trap came after this point)
reset()
t0 = time.time()
r = cli(["run", "--gpu", "0", "--cpus", "2", "--ram", "bogus", "--secs", "40"], {"FAKE_QEMU": "sleep"})
check("early-die-exit-nonzero", r.returncode != 0, r.stderr[-200:])
check("early-die-no-leftovers", not leftovers(), str(procs(TOKEN)))
check("early-die-fast", time.time() - t0 < 8, "%.1f s" % (time.time() - t0))

# 5. kill QEMU mid-run (kill -9): non-zero exit, nothing left
reset()
p = subprocess.Popen([CLI, "run"] + OPTS, env=dict(BASE, FAKE_QEMU="sleep"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
end = time.time() + 15
while time.time() < end and not [x for x in procs(TOKEN) if open("/proc/%d/cmdline" % x, "rb").read().startswith(TOKEN.encode() + b"\0")]:
    time.sleep(0.2)
for x in procs(TOKEN):
    if open("/proc/%d/cmdline" % x, "rb").read().startswith(TOKEN.encode() + b"\0"):
        os.kill(x, signal.SIGKILL)
try:
    out, err = p.communicate(timeout=20)
except subprocess.TimeoutExpired:
    p.kill(); out, err = p.communicate()
check("kill9-exit-nonzero", p.returncode not in (0, None), "rc=%s" % p.returncode)
check("kill9-no-leftovers", not leftovers(), str(procs(TOKEN)))

# 6. two runs at once get two run dirs (and one SIGTERM cleans its own)
reset()
ps = [subprocess.Popen([CLI, "run"] + OPTS, env=dict(BASE, FAKE_QEMU="sleep", FAKE_TOKEN=TOKEN + str(i)), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) for i in range(2)]
end = time.time() + 15
while time.time() < end and len([d for d in rundirs() if os.path.exists(d + "qemu.pid")]) < 2:
    time.sleep(0.2)
check("unique-run-dirs", len(rundirs()) == 2, str(rundirs()))
for q in ps:
    q.send_signal(signal.SIGTERM)
for q in ps:
    try:
        q.wait(20)
    except subprocess.TimeoutExpired:
        q.kill()
check("sigterm-no-leftovers", not leftovers(), str(procs(TOKEN)))

# 7. snapshot names: rejected before anything is touched
reset()
for n in ("..", ".", ".hid", "a/b", "a b", "a;b", "a..b", "-x", "x" * 65, "$(id)"):
    r = cli(["snapshot", n] + OPTS)
    check("snapshot-rejects-%r" % n, r.returncode != 0 and "invalid snapshot name" in r.stderr and os.path.isfile(HOME + "/vm/aux.img"), r.stderr[-150:])
r = cli(["resume", ".."] + OPTS)
check("resume-rejects-dotdot", r.returncode != 0 and "invalid snapshot name" in r.stderr)
check("names-leave-home-alone", os.path.isfile(HOME + "/vm/golden/root-sshd.qcow2") and not rundirs())

# ---- snapshots against a fake QEMU that serves QMP, so the helpers really run (the old fixtures failed at the QMP connect, before any
# staging happened, and passed on code that had no staging at all)
sys.path.insert(0, HERE + "/lib")
import importlib.util
sp = importlib.util.spec_from_file_location("snapctl", HERE + "/lib/snapctl.py")
sc = importlib.util.module_from_spec(sp); sp.loader.exec_module(sc)
QOPTS = ["--gpu", "0", "--cpus", "2", "--ram", "64M", "--secs", "60", "--settle", "0"]


def old_snapshot(name="keep"):
    subprocess.run(["rm", "-rf", HOME + "/snaps/" + name])
    os.makedirs(HOME + "/snaps/" + name)
    write(HOME + "/snaps/%s/meta.json" % name, '{"old": true}')
    write(HOME + "/snaps/%s/vm.state" % name, "old state")


def old_intact(name="keep"):
    try:
        return open(HOME + "/snaps/%s/vm.state" % name).read() == "old state" and json.load(open(HOME + "/snaps/%s/meta.json" % name)) == {"old": True}
    except OSError:
        return False


def no_staging():
    return not glob.glob(HOME + "/snaps/.stage-*") and not glob.glob(HOME + "/snaps/.old-*")


def make_resume_fixture():
    subprocess.run(["rm", "-rf", HOME + "/snaps/r1"])
    os.makedirs(HOME + "/snaps/r1")
    m = sc.meta_for(PREFIX + "/bin/qemu-system-aarch64", 2, "64M", "-global apple-gfx-mmio.disable-argument-buffers=on", 0)
    m.update({"fmt": "zstd", "host_wall": time.time()})
    json.dump(m, open(HOME + "/snaps/r1/meta.json", "w"))
    for n in ("aux", "root.qcow2", "vm.state"):
        write(HOME + "/snaps/r1/" + n, "x")


def wait_for(pred, limit=20):
    end = time.time() + limit
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def log_has(text):
    return any(text in open(f, errors="replace").read() for f in glob.glob(RUNS + "/*/snapctl.log"))


# 8. a save that completes replaces the old one; one that fails (the migration fails, after the staging exists) keeps the old one
reset()
old_snapshot()
r = cli(["snapshot", "keep"] + QOPTS, {"FAKE_QEMU": "qmp"}, timeout=90)
meta = json.load(open(HOME + "/snaps/keep/meta.json")) if os.path.exists(HOME + "/snaps/keep/meta.json") else {}
check("save-ok-exit-0", r.returncode == 0 and "old" not in meta and meta.get("fmt") == "zstd" and os.path.isfile(HOME + "/snaps/keep/vm.state"),
      "rc=%s %s %s" % (r.returncode, r.stdout[-400:], r.stderr[-300:]))
check("save-ok-clean", no_staging() and not rundirs() and not leftovers(), "%s %s" % (glob.glob(HOME + "/snaps/.*"), rundirs()))

reset()
old_snapshot()
t0 = time.time()
r = cli(["snapshot", "keep"] + QOPTS, {"FAKE_QEMU": "qmp", "FAKE_MIGRATE": "fail"}, timeout=90)
check("save-fails-exit-nonzero", r.returncode != 0, "rc=%s" % r.returncode)
check("save-fails-reached-the-migration", log_has("step 4 of 4") and log_has("SAVE failed: fake failure"), r.stdout[-400:])
check("save-fails-qemu-stopped", not leftovers() and time.time() - t0 < 40, "%.1f s %s" % (time.time() - t0, procs(TOKEN)))
check("save-fails-old-snapshot-kept", old_intact())
check("save-fails-no-staging-left", no_staging(), str(glob.glob(HOME + "/snaps/.*")))

# a helper that fails before the save (ssh never answers and QEMU is idle) is stopped, and the old snapshot stays
# 9. resume: loads fine, or the load fails (non-zero, QEMU stopped at once)
reset()
make_resume_fixture()
r = cli(["resume", "r1"] + OPTS, {"FAKE_QEMU": "qmp"}, timeout=90)
check("resume-ok-exit-0", r.returncode == 0 and not leftovers(), "rc=%s %s" % (r.returncode, r.stdout[-300:] + r.stderr[-300:]))
reset()
make_resume_fixture()
t0 = time.time()
r = cli(["resume", "r1"] + OPTS, {"FAKE_QEMU": "qmp", "FAKE_INCOMING": "fail"}, timeout=90)
check("resume-load-fails-exit-nonzero", r.returncode != 0, "rc=%s %s" % (r.returncode, r.stdout[-300:] + r.stderr[-300:]))
check("resume-load-fails-reached-the-load", log_has("RESUME failed: fake failure"))
check("resume-load-fails-qemu-stopped", not leftovers() and time.time() - t0 < 40, "%.1f s" % (time.time() - t0))
check("resume-load-fails-snapshot-kept", os.path.isfile(HOME + "/snaps/r1/vm.state"))
r = cli(["resume", "nosuch"] + OPTS, {"FAKE_QEMU": "qmp"})
check("resume-missing-says-where-to-look", r.returncode != 0 and "SNAPS=" in r.stderr, r.stderr[-300:])


# 10. signals. Ctrl-C or SIGTERM to the CLI ends it within seconds with 128+signal, stops everything it started (helper, tail,
# QEMU, nbd, ssh) and leaves no half snapshot; the older snapshot of that name is untouched.
def interrupted(sig, env, until, group=False, name="keep"):
    reset()
    old_snapshot()
    # always its own session: a CLI that hangs (old code) leaves children holding the pipes, so the whole group is killed after the wait
    p = subprocess.Popen([CLI, "snapshot", name] + QOPTS, env=dict(BASE, **env), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    ok = wait_for(until, 30)
    t = time.time()
    (os.killpg(p.pid, sig) if group else p.send_signal(sig))
    try:
        p.wait(timeout=10)
        took = time.time() - t
    except subprocess.TimeoutExpired:
        took = 99
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    out, err = p.communicate()
    return ok, p.returncode, took, out, err


for sig, want in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
    # the helper is inside its ssh retry loop (QEMU is up, ssh refuses): the case that used to hang for an hour
    ok, rc, took, out, err = interrupted(sig, {"FAKE_QEMU": "qmp", "FAKE_SSH": "fail"}, lambda: log_has("step 1 of 4"))
    n = signal.Signals(sig).name
    check("%s-during-ssh-wait-ends-fast" % n, ok and took < 10, "ok=%s took=%.1f s" % (ok, took))
    check("%s-during-ssh-wait-exit-%d" % (n, want), rc == want, "rc=%s" % rc)
    check("%s-during-ssh-wait-no-leftovers" % n, not leftovers() and not rundirs(), "%s %s" % (procs(T + "/home/runs"), rundirs()))
    check("%s-during-ssh-wait-old-kept" % n, old_intact() and no_staging(), str(glob.glob(HOME + "/snaps/.*")))
    # mid-save: the migration runs and never ends
    ok, rc, took, out, err = interrupted(sig, {"FAKE_QEMU": "qmp", "FAKE_MIGRATE": "hang"}, lambda: log_has("step 4 of 4"))
    check("%s-mid-save-ends-fast" % n, ok and took < 10, "ok=%s took=%.1f s" % (ok, took))
    check("%s-mid-save-exit-%d" % (n, want), rc == want, "rc=%s" % rc)
    check("%s-mid-save-no-leftovers" % n, not leftovers() and not rundirs(), "%s %s" % (procs(T + "/home/runs"), rundirs()))
    check("%s-mid-save-old-kept-no-half-snapshot" % n, old_intact() and no_staging(), str(glob.glob(HOME + "/snaps/.*")))

# SIGTERM to the whole process group (systemd stop, `timeout N snapintosh snapshot x`): the helper dies of it as well. Never exit 0, no snapshot.
ok, rc, took, out, err = interrupted(signal.SIGTERM, {"FAKE_QEMU": "qmp", "FAKE_MIGRATE": "hang"}, lambda: log_has("step 4 of 4"), group=True, name="fresh")
check("group-sigterm-ends-fast", ok and took < 10, "ok=%s took=%.1f s" % (ok, took))
check("group-sigterm-exit-nonzero", rc not in (0, None), "rc=%s" % rc)
check("group-sigterm-no-snapshot", not os.path.exists(HOME + "/snaps/fresh") and no_staging() and not leftovers(), str(glob.glob(HOME + "/snaps/*")))

# QEMU dies by itself while the helper still waits for ssh: the helper stops retrying, the CLI ends within seconds and says so
reset()
p = subprocess.Popen([CLI, "snapshot", "keep"] + QOPTS, env=dict(BASE, FAKE_QEMU="qmp", FAKE_SSH="fail"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                     start_new_session=True)
wait_for(lambda: log_has("step 1 of 4"), 30)
for x in procs("qmp.sock"):
    c = open("/proc/%d/cmdline" % x, "rb").read()
    if b"fakeqmp.py" in c and not c.startswith(b"timeout"):
        os.kill(x, signal.SIGKILL)
t = time.time()
try:
    p.wait(timeout=15)
except subprocess.TimeoutExpired:
    pass
try:
    os.killpg(p.pid, signal.SIGKILL)
except OSError:
    pass
out, err = p.communicate()
check("qemu-gone-helper-stops-retrying", time.time() - t < 10 and p.returncode not in (0, None), "rc=%s %.1f s" % (p.returncode, time.time() - t))
check("qemu-gone-no-leftovers", not leftovers(), str(procs(T + "/home/runs")))

# 11. a pid file never names a process to kill: only a qemu-nbd on our socket is stopped, and a stale file (any other process) is left alone
reset()
fn = T + "/fn.sh"
src = open(CLI).read()
m = re.search(r"^pstate\(\).*?^cleanup_reset\(\).*?$", src, re.S | re.M)
write(fn, (m.group(0) if m else "") + "\n")
victim = subprocess.Popen(["bash", "-c", 'exec -a "%s-victim" sleep 60' % TOKEN])
nbd_sock = T + "/x/nbd.sock"
write(T + "/bin/qemu-nbd", "#!/bin/bash\nmkfifo %s/ff 2>/dev/null; read -t 60 x <> %s/ff\n" % (T, T), 0o755)
real = subprocess.Popen([T + "/bin/qemu-nbd", "-k", nbd_sock, "-f", "qcow2"])
time.sleep(0.5)
for nm, pid, sock, dies in (("stale-pid-of-another-process-survives", victim.pid, nbd_sock, False), ("real-qemu-nbd-on-our-socket-stops", real.pid, nbd_sock, True),
                            ("qemu-nbd-on-another-socket-survives", real.pid, T + "/other.sock", False)):
    if nm.startswith("qemu-nbd-on-another") and real.poll() is not None:
        continue
    write(T + "/pidfile", "%d\n" % pid)
    subprocess.run(["bash", "-c", '. "%s"; LIB=/nonexistent; CL_NBDPID="%s"; CL_NBDSOCK="%s"; cleanup_all' % (fn, T + "/pidfile", sock)], capture_output=True)
    time.sleep(0.3)
    alive = (victim if pid == victim.pid else real).poll() is None
    check(nm, alive != dies, "alive=%s" % alive)
victim.kill(); real.kill(); victim.wait(); real.wait()
dead = subprocess.run(["bash", "-c", "exit 0"]); write(T + "/pidfile", "2999999\n")   # a pid file naming a process that is gone: silent
r = subprocess.run(["bash", "-c", '. "%s"; LIB=/nonexistent; CL_NBDPID="%s"; CL_NBDSOCK="%s"; cleanup_all' % (fn, T + "/pidfile", nbd_sock)], capture_output=True, text=True)
check("stale-pid-of-a-dead-process-is-silent", r.returncode == 0 and r.stderr == "", r.stderr)

# 12. `&`, glob characters and spaces in SNAPINTOSH_HOME reach QEMU unchanged (bash 5.2+ patsub_replacement: `&` in a replacement is the match)
reset()
HOME2 = T + "/h&m[a-z]*e x"
subprocess.run(["cp", "-r", HOME, HOME2])
args = T + "/args2"
r = cli(["run", "--gpu", "0", "--cpus", "2", "--ram", "64M", "--secs", "40"], {"FAKE_QEMU": "exit:0", "FAKE_ARGS": args, "SNAPINTOSH_HOME": HOME2, "KEEP_RUNS": "1"})
rd = glob.glob(glob.escape(HOME2) + "/runs/run-*/")
a = open(args).read().split("\n") if os.path.exists(args) else []
ok = bool(rd) and ("file:" + rd[0] + "serial.log") in a and ("unix:" + rd[0] + "qmp.sock,server=on,wait=off") in a and \
     any(x.endswith("file.server.path=" + rd[0] + "nbd.sock") for x in a) and not any("XXXXXX" in x for x in a)
check("special-chars-in-home-reach-qemu-unchanged", r.returncode == 0 and ok, "rc=%s %s %s" % (r.returncode, [x for x in a if "run" in x][:4], r.stderr[-200:]))
subprocess.run(["rm", "-rf", HOME2])

# 13. unix socket paths: the name is capped so nbd.sock, qmp.sock and ssh.ctl (plus ssh's 17 byte bind suffix) stay under 108 bytes
reset()
runs_len = len(RUNS.encode())
cap = 107 - 17 - 8 - 7 - 1 - runs_len
r = cli(["snapshot", "n" * cap, "--dry-run", "--gpu", "0"])
check("name-at-the-cap-accepted", r.returncode == 0, r.stderr[-200:])
r = cli(["snapshot", "n" * (cap + 1), "--dry-run", "--gpu", "0"])
check("name-over-the-cap-refused-with-reason", r.returncode != 0 and "too long" in r.stderr and "%d bytes" % cap in r.stderr and not rundirs(), r.stderr[-300:])
check("cap-keeps-every-socket-under-108", len((RUNS + "/" + "n" * cap + "-XXXXXX/ssh.ctl").encode()) + 17 <= 107)
long_home = T + "/" + "h" * 80
r = cli(["run", "--dry-run", "--gpu", "0"], {"SNAPINTOSH_HOME": long_home})
check("home-too-long-refused-with-reason", r.returncode != 0 and "SNAPINTOSH_HOME is too long" in r.stderr, r.stderr[-300:])
subprocess.run(["rm", "-rf", long_home])

# 14. a guest password exported in the caller's shell does not reach QEMU, qemu-nbd or ssh
reset()
envf = T + "/env"
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0", "FAKE_ENV": envf, "SNAPINTOSH_GUEST_PASSWORD": "sekret-pw"})
check("guest-password-env-not-inherited", os.path.exists(envf) and "sekret-pw" not in open(envf).read() and "SNAPINTOSH_GUEST_PASSWORD" not in open(envf).read(), r.stderr[-200:])

# 15. the `timeout` wrapper around QEMU is SIGKILLed. QEMU dies with it (PR_SET_PDEATHSIG), it must not live on with the ssh port and the pack lock
def qemu_procs():
    return [x for x in procs("qmp.sock") if b"fakeqmp.py" in open("/proc/%d/cmdline" % x, "rb").read()]


reset()
# a `timeout` like GNU coreutils': its death leaves the child running (uutils' timeout, the Ubuntu default now, takes the child down with it, which would hide the bug)
write(T + "/gnubin/timeout", """#!/usr/bin/env python3
import os, signal, subprocess, sys
os.setpgid(0, 0)   # like timeout(1) without --foreground: its own process group
a = sys.argv[1:]
while a and a[0].startswith("-"):
    a = a[2:] if a[0] == "-k" else a[1:]
p = subprocess.Popen(a[1:])
signal.signal(signal.SIGTERM, lambda *x: p.terminate())
sys.exit(p.wait() & 255)
""", 0o755)
p = subprocess.Popen([CLI, "run"] + OPTS, env=dict(BASE, FAKE_QEMU="qmp", PATH=T + "/gnubin:" + BASE["PATH"]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
ok = wait_for(lambda: bool(glob.glob(RUNS + "/*/qmp.sock")) and bool(qemu_procs()), 30)
wrappers = [x for x in procs("qmp.sock") if b"/gnubin/timeout" in open("/proc/%d/cmdline" % x, "rb").read()]
p.send_signal(signal.SIGSTOP)   # the CLI is still waiting (a resume's helper keeps it for a long time) while its wrapper dies
for x in wrappers:
    os.kill(x, signal.SIGKILL)
gone = wait_for(lambda: not qemu_procs(), 5)
orphans = qemu_procs()
try:
    os.killpg(p.pid, signal.SIGKILL)
except OSError:
    pass
p.communicate()
for x in orphans:
    try:
        os.kill(x, signal.SIGKILL)
    except OSError:
        pass
check("qemu-dies-with-its-killed-timeout-wrapper", ok and bool(wrappers) and gone, "ok=%s wrappers=%s orphans=%s" % (ok, wrappers, orphans))
reset()


# 15b. a QEMU left by a snapintosh that is gone (the CLI itself SIGKILLed) is found by the next command and stopped; a QEMU whose snapintosh lives is left alone
def stat_start(pid):
    return open("/proc/%d/stat" % pid).read().rsplit(") ", 1)[1].split()[19]


MYNS = re.sub(r"\D", "", os.readlink("/proc/self/ns/pid"))


def fake_orphan(name, owner):
    rd = RUNS + "/" + name
    os.makedirs(rd, exist_ok=True)
    if owner is not None:
        write(rd + "/cli.pid", "%d:%s:%s\n" % (owner, stat_start(owner), MYNS))
    return subprocess.Popen(["python3", T + "/fakeqmp.py", "-qmp", "unix:%s/qmp.sock,server=on,wait=off" % rd, "-x", TOKEN], start_new_session=True)


reset()
gone_owner = subprocess.Popen(["true"]); gone_owner.wait()
write(RUNS + "/stale-aaaaaa/cli.pid", "%d:%s:%s\n" % (gone_owner.pid, "1", MYNS))
stale = fake_orphan("stale-aaaaaa", None)
write(RUNS + "/stale-aaaaaa/cli.pid", "%d:%s:%s\n" % (gone_owner.pid, "1", MYNS))
write(RUNS + "/stale-aaaaaa/snapctl.log", "")
# what a SIGKILLed snapintosh also leaves: its progress tail and a helper that names the run dir
stale_tail = subprocess.Popen(["tail", "-n", "+1", "-F", RUNS + "/stale-aaaaaa/snapctl.log"], start_new_session=True)
stale_helper = subprocess.Popen(["python3", "-c", "import time; time.sleep(120)", RUNS + "/stale-aaaaaa"], start_new_session=True)
mine = fake_orphan("alive-bbbbbb", os.getpid())
mine_helper = subprocess.Popen(["python3", "-c", "import time; time.sleep(120)", RUNS + "/alive-bbbbbb"], start_new_session=True)
time.sleep(0.5)
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
time.sleep(0.5)
for x in (stale, stale_tail, stale_helper):
    try:
        x.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
check("stale-qemu-of-a-dead-snapintosh-is-stopped-and-said", stale.poll() is not None and "of an earlier snapintosh that died" in r.stderr and str(stale.pid) in r.stderr and r.returncode == 0,
      "poll=%s rc=%s %s" % (stale.poll(), r.returncode, r.stderr[-300:]))
check("stale-tail-and-helper-of-a-dead-snapintosh-are-stopped", stale_tail.poll() is not None and stale_helper.poll() is not None, "%s %s" % (stale_tail.poll(), stale_helper.poll()))
check("qemu-and-helper-of-a-live-snapintosh-are-left-alone", mine.poll() is None and mine_helper.poll() is None, "poll=%s %s" % (mine.poll(), mine_helper.poll()))
for x in (mine, mine_helper, stale_tail, stale_helper):
    x.kill(); x.wait()
reset()


# 15c. stop_tree must catch a child that is being forked at the moment of the signal. A parent forks as fast as it can; after stop_tree
# nothing of its tree may be left (the old stop_tree listed the tree first and killed afterwards: the children forked in between survived)
reset()
write(T + "/forker.sh", '#!/bin/bash\nwhile :; do ( exec -a "%s-kid" sleep 100 ) & sleep 0.002; done\n' % TOKEN, 0o755)
fk = subprocess.Popen([T + "/forker.sh"])
time.sleep(0.4)
subprocess.run(["bash", "-c", '. "%s"; stop_tree %d' % (fn, fk.pid)], capture_output=True, timeout=30)
fk.wait(timeout=5)
time.sleep(0.3)
left = procs(TOKEN + "-kid")
for x in left:
    try:
        os.kill(x, signal.SIGKILL)
    except OSError:
        pass
check("stop-tree-leaves-no-child-forked-during-the-stop", not left, "%d left" % len(left))

# a SIGTERM while resume runs its settime step (ssh never answers): nothing of the helper chain survives
reset()
make_resume_fixture()
e = dict(BASE, FAKE_QEMU="qmp", FAKE_SSH="fail", PATH=T + "/gnubin:" + BASE["PATH"]); e.pop("SNAPINTOSH_NO_SETTIME")
p = subprocess.Popen([CLI, "resume", "r1"] + OPTS, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
ok = wait_for(lambda: bool(glob.glob(RUNS + "/*/resume.json")), 30)
time.sleep(1.0)
os.killpg(p.pid, signal.SIGTERM)   # the whole group (systemd stop, a driver's killpg): the helper's subshell dies before the trap runs, its `timeout` has a group of its own
try:
    p.wait(timeout=10)
except subprocess.TimeoutExpired:
    pass
time.sleep(0.3)
left = procs(T + "/home/runs")
try:
    os.killpg(p.pid, signal.SIGKILL)
except OSError:
    pass
p.communicate()
check("group-sigterm-during-settime-leaves-nothing", ok and p.returncode not in (0, None) and not left, "ok=%s rc=%s left=%s" % (ok, p.returncode, left))

# a bad SETTIME_TIMEOUT stops `resume` before QEMU starts; the budget the helper and its `timeout` get is the host-scaled one (6 threads: 600 + 30)
reset()
make_resume_fixture()
r = cli(["resume", "r1"] + OPTS, {"SETTIME_TIMEOUT": "10m", "FAKE_QEMU": "sleep"})
check("bad-settime-timeout-stops-resume-before-qemu", r.returncode != 0 and "expected a number of seconds" in r.stderr and not rundirs() and not leftovers(1.0), r.stderr[-200:])
write(T + "/shim/timeout", '#!/bin/bash\necho "$@" >> "$TIMEOUT_LOG"\nexec "%s" "$@"\n' % shutil.which("timeout"), 0o755)
for nm, thr, extra, want in (("6-threads", "6", {}, "-k 5 630 "), ("16-threads", "16", {}, "-k 5 150 "), ("override", "6", {"SETTIME_TIMEOUT": "40"}, "-k 5 70 ")):
    reset()
    make_resume_fixture()
    open(T + "/timeout.log", "w").close()
    e = dict(BASE, FAKE_QEMU="qmp", FAKE_SSH="fail", PATH=T + "/shim:" + BASE["PATH"], TIMEOUT_LOG=T + "/timeout.log", SNAPINTOSH_CHECK_THREADS=thr, **extra); e.pop("SNAPINTOSH_NO_SETTIME")
    p = subprocess.Popen([CLI, "resume", "r1"] + OPTS, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    wait_for(lambda: want in open(T + "/timeout.log").read(), 30)
    got = open(T + "/timeout.log").read()
    try:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.communicate()
    check("settime-hard-limit-%s" % nm, want in got and (" 600 " in got or "40" in got or nm == "16-threads"), got)

# 16. (found by the pack size-cap measurement): the pack is compacted after a Ctrl-C or SIGTERM as well as after a normal exit. The trap used to skip it,
# so a pack that was only ever ended with Ctrl-C was never compacted, grew to its limit and then dropped every new translation for the rest of each boot
write(PREFIX + "/bin/qemu-pcache-compact", '#!/bin/sh\necho "$1: nothing to do"\nexit 1\n', 0o755)
CLOG = HOME + "/pcache/compact.log"
PCENV = {"PCCOMPACT": "1", "FAKE_PCLINE": HOME + "/pcache/abc.pack"}
for nm, sig in (("normal-exit", None), ("sigint", signal.SIGINT), ("sigterm", signal.SIGTERM)):
    reset()
    subprocess.run(["rm", "-f", CLOG])
    if sig is None:
        cli(["run"] + OPTS, dict(PCENV, FAKE_QEMU="exit:0"))
    else:
        p = subprocess.Popen([CLI, "run"] + OPTS, env=dict(BASE, FAKE_QEMU="qmp", **PCENV), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        wait_for(lambda: bool(glob.glob(RUNS + "/*/qmp.sock")), 30)
        time.sleep(0.5)
        p.send_signal(sig)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
    got = wait_for(lambda: os.path.exists(CLOG) and "compact end" in open(CLOG).read(), 10)
    check("pack-compacted-after-%s" % nm, got, open(CLOG).read() if os.path.exists(CLOG) else "no compact.log")

# 15d. reap_stale kills only what provably belongs to a dead run of this pid namespace; everything else is skipped and said
def stale_case(name, cli_pid_text, helper_cmd=None):
    """a run dir with a cli.pid text and a helper naming it; returns (helper, cli result). The helper is killed afterwards."""
    reset()
    rd = RUNS + "/" + name
    os.makedirs(rd, exist_ok=True)
    if cli_pid_text is not None:
        write(rd + "/cli.pid", cli_pid_text)
    h = subprocess.Popen(helper_cmd or ["python3", "-c", "import time; time.sleep(120)", rd], start_new_session=True)
    time.sleep(0.4)
    r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
    time.sleep(0.4)
    alive = h.poll() is None
    h.kill(); h.wait()
    return alive, r


dead = subprocess.Popen(["true"]); dead.wait()
alive, r = stale_case("ns-aaaaaa", "%d:1:%s\n" % (dead.pid, str(int(MYNS) + 1)))
check("reap-skips-a-run-of-another-pid-namespace", alive and "skipped pid" in r.stderr and "another pid namespace" in r.stderr, "alive=%s %s" % (alive, r.stderr[-300:]))
alive, r = stale_case("old-aaaaaa", "%d:1\n" % dead.pid)
check("reap-skips-a-cli-pid-without-a-namespace", alive and "skipped pid" in r.stderr and "namespace" in r.stderr, "alive=%s %s" % (alive, r.stderr[-300:]))
alive, r = stale_case("nopid-aaaaaa", None)
check("reap-skips-a-run-dir-without-cli-pid", alive and "skipped pid" in r.stderr, "alive=%s %s" % (alive, r.stderr[-300:]))
alive, r = stale_case("dead-aaaaaa", "%d:1:%s\n" % (dead.pid, MYNS))
check("reap-still-stops-a-provably-dead-run", not alive and "of an earlier snapintosh that died" in r.stderr, "alive=%s %s" % (alive, r.stderr[-300:]))
# a helper that started BEFORE the dead snapintosh did cannot be its child: skipped
reset()
rd = RUNS + "/early-aaaaaa"; os.makedirs(rd, exist_ok=True)
early = subprocess.Popen(["python3", "-c", "import time; time.sleep(120)", rd], start_new_session=True)
write(rd + "/cli.pid", "%d:%d:%s\n" % (dead.pid, int(stat_start(early.pid)) + 1000, MYNS))
time.sleep(0.3)
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
check("reap-skips-a-process-older-than-the-dead-cli", early.poll() is None and "skipped pid" in r.stderr, r.stderr[-300:])
early.kill(); early.wait()
# a process of another pid namespace (when the kernel lets us make one): visible in /proc, never killed
if subprocess.run(["unshare", "--user", "--map-root-user", "--pid", "--fork", "--mount-proc", "true"], capture_output=True).returncode == 0:
    reset()
    rd = RUNS + "/nsproc-aaaaaa"; os.makedirs(rd, exist_ok=True)
    write(rd + "/cli.pid", "%d:1:%s\n" % (dead.pid, MYNS))
    other = subprocess.Popen(["unshare", "--user", "--map-root-user", "--pid", "--fork", "python3", "-c", "import time; time.sleep(120)", rd], start_new_session=True)
    time.sleep(1.0)
    inner = [x for x in procs(rd) if x != other.pid and open("/proc/%d/cmdline" % x, "rb").read().startswith(b"python3")]
    r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
    check("reap-never-kills-a-process-of-another-pid-namespace", bool(inner) and all(os.path.exists("/proc/%d" % x) for x in inner), "inner=%s %s" % (inner, r.stderr[-300:]))
    os.killpg(other.pid, signal.SIGKILL); other.wait()
reset()

# run_procs: the ssh ControlPersist master renames itself to "ssh: RUNDIR/ssh.ctl [mux]" and is found; a vanishing process is silent
fn_rp = re.search(r"^pstart\(\).*$", open(CLI).read(), re.M).group(0) + "\n" + "".join(
    re.search(r"^%s\(\) \{.*?^\}" % n, open(CLI).read(), re.M | re.S).group(0) + "\n" for n in ("pidns", "run_procs"))
rd = RUNS + "/mux-aaaaaa"; os.makedirs(rd, exist_ok=True)
mux = subprocess.Popen(["bash", "-c", 'exec -a "ssh: %s/ssh.ctl [mux]" sleep 100' % rd])
time.sleep(0.3)
found = subprocess.run(["bash", "-c", fn_rp + '\nPIDNS=$(pidns)\nrun_procs "%s"' % rd], capture_output=True, text=True).stdout.split()
check("run-procs-finds-the-ssh-controlpersist-master", str(mux.pid) in found, "%s vs %s" % (mux.pid, found))
mux.kill(); mux.wait()
churn = subprocess.Popen(["bash", "-c", "while :; do /bin/true & /bin/true & wait; done"])
noise = ""
for _ in range(15):
    noise += subprocess.run(["bash", "-c", fn_rp + '\nPIDNS=$(pidns)\nrun_procs /nonexistent/run'], capture_output=True, text=True).stderr
churn.kill(); churn.wait()
check("run-procs-is-silent-about-processes-that-vanish", noise == "", noise[:200])

# every number the CLI does arithmetic on is base 10: a leading zero is not octal
r = cli(["run"] + OPTS[:-2] + ["--secs", "08"], {"FAKE_QEMU": "exit:0"})
check("secs-with-a-leading-zero-is-decimal", r.returncode == 0, r.stderr[-200:])
r = cli(["run", "--gpu", "0", "--dry-run", "--vnc", "05900"])
check("vnc-port-with-a-leading-zero-is-decimal", r.returncode == 0 and "127.0.0.1:0" in r.stdout + r.stderr, r.stdout[-200:] + r.stderr[-200:])
r = cli(["run", "--gpu", "0", "--dry-run", "--secs", "abc"])
check("secs-must-be-a-number", r.returncode != 0 and "--secs" in r.stderr, r.stderr[-200:])
r = cli(["check-host"], {"SNAPINTOSH_CHECK_MEM_KB": "016000000"})
check("check-host-mem-with-a-leading-zero-is-decimal", "RAM 15 GiB" in r.stdout, r.stdout[-300:] + r.stderr[-200:])
wb = re.search(r"^warm_boots\(\) \{.*?^\}", open(CLI).read(), re.M | re.S).group(0)
dec = re.search(r"^dec_into\(\) \{.*?^\}", open(CLI).read(), re.M | re.S).group(0)
os.makedirs(T + "/wv/golden", exist_ok=True); write(T + "/wv/golden/root-sshd.qcow2", "x")
r = subprocess.run(["bash", "-c", 'set -euo pipefail\ndie() { echo "$*" >&2; exit 1; }; ok() { :; }; warn() { :; }\n' + dec + "\n" + wb +
                    '\nVM=%s/wv; USE_PCACHE=1; PCACHE_DIR=%s/pc; launch() { LAUNCH_RC=0; }; warm_boots 08' % (T, T)], capture_output=True, text=True)
check("warm-boots-with-a-leading-zero-is-decimal", r.returncode == 0 and r.stdout.count("warm-up boot") == 8, r.stdout[-200:] + r.stderr[-200:])

# the clock-step line carries the helper's exit status (a crash or a hard kill is not a clean give-up)
write(T + "/shim2/timeout", '#!/bin/bash\ncase " $* " in *snapctl.py*) exit 124 ;; esac\nexec "%s" "$@"\n' % shutil.which("timeout"), 0o755)
reset()
make_resume_fixture()
e = dict(BASE, FAKE_QEMU="qmp", FAKE_SSH="fail", PATH=T + "/shim2:" + BASE["PATH"]); e.pop("SNAPINTOSH_NO_SETTIME")
p = subprocess.Popen([CLI, "resume", "r1"] + OPTS, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
wait_for(lambda: any("SETTIME did not confirm" in open(f).read() for f in glob.glob(RUNS + "/*/snapctl.log")), 30)
line = "".join(open(f).read() for f in glob.glob(RUNS + "/*/snapctl.log"))
try:
    os.killpg(p.pid, signal.SIGTERM); p.wait(timeout=10)
except (OSError, subprocess.TimeoutExpired):
    pass
try:
    os.killpg(p.pid, signal.SIGKILL)
except OSError:
    pass
p.communicate()
check("settime-line-carries-the-helper-exit-status", "helper exit status 124" in line, line[-300:])

# golden phase 1 runs QEMU and helpers in $VM/golden/tmp, not in a run dir. A phase 1 whose snapintosh was SIGKILLed is reaped like
# a dead run (same cli.pid), and a half written root-sshd.qcow2 is set aside so `make-disk golden --again` never builds on it
reset()
g1 = HOME + "/vm/golden"; os.makedirs(g1 + "/tmp", exist_ok=True)
write(g1 + "/tmp/cli.pid", "%d:1:%s\n" % (dead.pid, MYNS))
h1 = subprocess.Popen(["python3", "-c", "import time; time.sleep(120)", g1 + "/tmp/memfd.path"], start_new_session=True)
time.sleep(0.4)
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
time.sleep(0.4)
check("reap-stops-the-leftovers-of-a-killed-golden-phase-1", h1.poll() is not None and "of an earlier snapintosh that died" in r.stderr, "%s %s" % (h1.poll(), r.stderr[-300:]))
h1.kill(); h1.wait()
src = open(CLI).read()
fn_g1 = "".join(re.search(r"^%s\(\) \{.*?^\}" % n, src, re.M | re.S).group(0) + "\n" for n in
                ("stop_tree", "run_procs", "stop_run_procs", "kill_nbd", "reap_stale", "cli_dead", "reap_skip", "golden_unfinished_phase1"))
fn_g1 = re.search(r"^pstart\(\).*$", src, re.M).group(0) + "\n" + re.search(r"^pidns\(\).*$", src, re.M).group(0) + "\n" + fn_g1


def phase1_case(cli_pid, marker=True):
    shutil.rmtree(g1, ignore_errors=True); os.makedirs(g1 + "/tmp")
    write(g1 + "/root-sshd.qcow2", "half")
    if cli_pid: write(g1 + "/tmp/cli.pid", cli_pid)
    if marker: write(g1 + "/tmp/phase1.running", "")
    h = subprocess.Popen(["python3", "-c", "import time; time.sleep(120)", g1 + "/tmp/memfd.path"], start_new_session=True)
    time.sleep(0.4)
    r = subprocess.run(["bash", "-c", 'set -uo pipefail\nwarn() { echo "[warn] $*"; }; die() { echo "snapintosh: $*" >&2; exit 1; }\nRUNS=%s/none\n' % T + fn_g1 + "\nPIDNS=$(pidns)\ngolden_unfinished_phase1 %s" % g1],
                       capture_output=True, text=True)
    alive = h.poll() is None
    h.kill(); h.wait()
    return alive, r


alive, r = phase1_case("%d:1:%s\n" % (dead.pid, MYNS))
check("golden-phase-1-killed-leftovers-stopped", not alive, r.stderr[-300:])
check("golden-phase-1-half-overlay-set-aside", not os.path.exists(g1 + "/root-sshd.qcow2") and os.path.exists(g1 + "/root-sshd.failed.qcow2") and not os.path.exists(g1 + "/tmp/phase1.running") and "without --again" in r.stdout, r.stdout[-300:])
alive, r = phase1_case("%d:%s:%s\n" % (os.getpid(), stat_start(os.getpid()), MYNS))
check("golden-phase-1-of-a-live-snapintosh-is-left-alone", alive and os.path.exists(g1 + "/root-sshd.qcow2"), r.stdout[-200:] + r.stderr[-200:])
check("golden-refuses-while-another-phase-1-runs", r.returncode != 0 and "still running" in r.stderr, r.stderr[-200:])
alive, r = phase1_case(None, marker=False)
check("a-finished-golden-keeps-its-overlay", os.path.exists(g1 + "/root-sshd.qcow2") and not os.path.exists(g1 + "/root-sshd.failed.qcow2"), r.stdout[-200:])
shutil.rmtree(g1, ignore_errors=True)
reset()


write(HOME + "/vm/golden/root-sshd.qcow2", "golden")   # the golden tests above removed it
# 16. kill -9 of the CLI during a save. The next command stops what is left AND removes the dead run dir and the staging
# directory (it holds the guest RAM); the old snapshot is untouched.
reset()
old_snapshot()
p = subprocess.Popen([CLI, "snapshot", "keep"] + QOPTS, env=dict(BASE, FAKE_QEMU="qmp", FAKE_MIGRATE="hang"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
seen = wait_for(lambda: log_has("step 4 of 4") and bool(glob.glob(HOME + "/snaps/.stage-*")), 30)
os.kill(p.pid, signal.SIGKILL); p.wait()
check("kill9-save-left-the-staging-and-run-dir", seen and bool(rundirs()) and bool(glob.glob(HOME + "/snaps/.stage-*")), "%s %s" % (rundirs(), glob.glob(HOME + "/snaps/.*")))
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
check("kill9-save-next-command-removes-staging-and-run-dir", r.returncode == 0 and no_staging() and not rundirs(), "rc=%s %s %s %s" % (r.returncode, rundirs(), glob.glob(HOME + "/snaps/.*"), r.stderr[-300:]))
check("kill9-save-next-command-says-so", "removed run dir" in r.stderr, r.stderr[-400:])
check("kill9-save-old-snapshot-intact-and-nothing-running", old_intact() and not leftovers(), str(procs(T + "/home/runs")))

# the same in the boot phase (step 1, ssh not answering yet): a small staging dir and the run dir
reset()
old_snapshot()
p = subprocess.Popen([CLI, "snapshot", "keep"] + QOPTS, env=dict(BASE, FAKE_QEMU="qmp", FAKE_SSH="fail"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
seen = wait_for(lambda: log_has("step 1 of 4") and bool(glob.glob(HOME + "/snaps/.stage-*")), 30)
os.kill(p.pid, signal.SIGKILL); p.wait()
before = (bool(rundirs()), bool(glob.glob(HOME + "/snaps/.stage-*")))
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
check("kill9-boot-phase-next-command-cleans-up", seen and before == (True, True) and r.returncode == 0 and no_staging() and not rundirs() and old_intact() and not leftovers(), "%s %s rc=%s %s %s" % (seen, before, r.returncode, rundirs(), r.stderr[-200:]))

# a failed run keeps its run dir for the logs, and the next command must not take it for a leak
reset()
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:3"})
kept = rundirs()
r2 = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
check("failed-run-dir-survives-the-next-command", r.returncode != 0 and len(kept) == 1 and rundirs() == kept, "%s %s" % (kept, rundirs()))

# a dead run dir is removed only with the proof reap_stale uses; a `keep` marker, a live owner, and a run dir without cli.pid stay
reset()
gone2 = subprocess.Popen(["true"]); gone2.wait()
for n in ("dead-aaaaaa", "kept-cccccc"):
    write(RUNS + "/%s/cli.pid" % n, "%d:%s:%s\n" % (gone2.pid, "1", MYNS)); write(RUNS + "/%s/snapctl.log" % n, "log")
write(RUNS + "/kept-cccccc/keep", "")
write(RUNS + "/alive-bbbbbb/cli.pid", "%d:%s:%s\n" % (os.getpid(), stat_start(os.getpid()), MYNS))
write(RUNS + "/nopid-dddddd/snapctl.log", "log")
write(RUNS + "/foreign-eeeeee/cli.pid", "%d:%s:%s\n" % (gone2.pid, "1", "4026539999"))   # another pid namespace: not ours to judge
r = cli(["run"] + OPTS, {"FAKE_QEMU": "exit:0"})
left = sorted(os.path.basename(x.rstrip("/")) for x in rundirs())
check("dead-run-dir-removed-live-keep-nopid-foreign-stay", left == ["alive-bbbbbb", "foreign-eeeeee", "kept-cccccc", "nopid-dddddd"], "%s %s" % (left, r.stderr[-300:]))
reset()

# 17. a save with an app open checks that its window is on screen. FAKE_APP in the fake ssh above.
def app_save(extra, env, name="keep"):
    reset(); old_snapshot()
    r = cli(["snapshot", name] + QOPTS + extra, dict({"FAKE_QEMU": "qmp", "APPWAIT": "1", "APPSTABLE": "0"}, **env), timeout=90)
    return r, r.stdout + "\n".join(open(f, errors="replace").read() for f in glob.glob(RUNS + "/*/snapctl.log"))


r, lg = app_save(["--app", "Safari"], {"FAKE_APP": "nowin"})
check("app-not-on-screen-refuses-with-status-5", r.returncode == 5 and "SAVE refused: Safari is not on screen" in lg and "NOT saved" in r.stderr, "rc=%s %s %s" % (r.returncode, lg[-300:], r.stderr[-200:]))
check("app-refusal-keeps-no-run-dir-and-says-it-once", not rundirs() and "helper failed" not in r.stderr and r.stderr.count("NOT saved") == 1, "%s %s" % (rundirs(), r.stderr[-300:]))
check("app-refusal-keeps-old-snapshot-and-leaves-nothing", old_intact() and no_staging() and not leftovers(), str(glob.glob(HOME + "/snaps/.*")))
r, lg = app_save(["--app", "Safari"], {"FAKE_APP": "young"})
check("safari-page-process-restarting-refuses", r.returncode == 5 and "only 3 s old" in lg, "rc=%s %s" % (r.returncode, lg[-300:]))
r, lg = app_save(["--app", "Safari"], {"FAKE_APP": "ready"})
check("app-on-screen-saves", r.returncode == 0 and "Safari is on screen" in lg and os.path.isfile(HOME + "/snaps/keep/meta.json") and "old" not in open(HOME + "/snaps/keep/meta.json").read(), "rc=%s %s" % (r.returncode, lg[-300:]))
r, lg = app_save([], {"FAKE_APP": "nowin"})
check("running-safari-not-on-screen-only-warns", r.returncode == 0 and "warning: Safari is running but not on screen" in lg, "rc=%s %s" % (r.returncode, lg[-300:]))
r, lg = app_save([], {})
check("no-app-no-check", r.returncode == 0 and "checking that" not in lg, "rc=%s %s" % (r.returncode, lg[-300:]))
r, lg = app_save(["--app", "Safari"], {})
check("guest-cannot-say-warns-and-saves", r.returncode == 0 and "warning: could not ask the guest" in lg, "rc=%s %s" % (r.returncode, lg[-300:]))
r, lg = app_save(["--app", "Safari"], {"FAKE_APP": "nowin", "APPWAIT": "0"})
check("appwait-0-turns-the-check-off", r.returncode == 0 and "checking that" not in lg, "rc=%s %s" % (r.returncode, lg[-300:]))

# --open URL opens through the detached helper, after the Dock and Finder, and requires the window
sshlog = T + "/ssh.log"
r, lg = app_save(["--open", "http://10.0.2.2:8000/a.html"], {"FAKE_APP": "ready", "FAKE_SSHLOG": sshlog})
cmds = open(sshlog).read() if os.path.exists(sshlog) else ""
check("open-uses-the-detached-open-helper", r.returncode == 0 and "POSIX::setsid()" in cmds and "launchctl asuser 501 open -a Safari http://10.0.2.2:8000/a.html" in cmds and "SAVE: open Safari: running" in lg, "rc=%s %s %s" % (r.returncode, cmds[-300:], lg[-300:]))
check("open-does-not-wait-for-open-to-return", "POSIX::setsid()" in cmds and cmds.count("&") >= 1)
for bad in (["--open", "http://x/a;rm -rf ~"], ["--open", "ftp://x/"], ["--open", "http://x/$(id)"], ["--app", "Safari;id"], ["--app", ""]):
    r = cli(["snapshot", "keep"] + QOPTS + bad)
    check("bad-%s-rejected" % "-".join(bad)[:24], r.returncode != 0 and ("--open URL" in r.stderr or "--app NAME" in r.stderr), "rc=%s %s" % (r.returncode, r.stderr[-200:]))
r = cli(["run"] + OPTS + ["--app", "Safari"])
check("app-on-run-rejected", r.returncode != 0 and "belong to snapshot" in r.stderr, r.stderr[-200:])
reset()
reset()
subprocess.run(["rm", "-rf", T])
sys.exit(1 if fails else 0)
