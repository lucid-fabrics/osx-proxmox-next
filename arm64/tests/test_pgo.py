"""`snapintosh build --pgo` (R30): the profile checks of lib/pgoctl.py, the refusals of the flag, the plan it prints and the clean-up after
a kill. No compiler runs: the QEMU the build would fetch is a few-line fake whose `configure` stays alive, so the tests reach the
point where the instrumented build is in progress inside the private mount namespace and then end the CLI the hard way."""
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(HERE, "snapintosh")
CTL = os.path.join(HERE, "lib", "pgoctl.py")
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else "  " + str(extra)))
    fails += 0 if cond else 1


def ctl(*a):
    return subprocess.run([sys.executable, "-I", CTL, *a], capture_output=True, text=True)


tmp = tempfile.mkdtemp(prefix="pgotest-")

# ---- check-log: what a profile that does not match looks like
CLEAN = """[1/9] Compiling C object libqemuutil.a.p/meson-generated_.._trace_trace-target_arm_hvf.c.o
/x/trace.c:1:1: warning: 'libqemuutil.a.p#trace.c.gcda' profile count data file not found [-Wmissing-profile]
[2/9] Compiling C object libqemu-aarch64-softmmu.a.p/target_arm_tcg_translate.c.o
[3/9] Compiling C object libsystem.a.p/hw_vmapple_cfg.c.o
[4/9] Compiling C object tests/test-x.p/test-x.c.o
/x/h.h:3:1: warning: profile for function 'f' not found in profile data [-Wmissing-profile]
[5/9] Linking target qemu-system-aarch64
"""


def log(name, text):
    p = os.path.join(tmp, name)
    open(p, "w").write(text)
    return p


r = ctl("check-log", log("clean.log", CLEAN))
check("check-log accepts missing profiles of objects the instrumented build never linked", r.returncode == 0, r.stderr)
r = ctl("check-log", log("fn.log", CLEAN.replace("[3/9]", "/x/q.h:3:1: warning: profile for function 'g' not found in profile data [-Wmissing-profile]\n[3/9]")))
check("check-log refuses a function without a profile in target/arm", r.returncode == 1 and "target_arm_tcg_translate" in r.stderr, r.stderr)
r = ctl("check-log", log("file.log", CLEAN.replace("[4/9]", "/x/v.c:1:1: warning: 'libsystem.a.p#hw_vmapple_cfg.c.gcda' profile count data file not found [-Wmissing-profile]\n[4/9]")))
check("check-log refuses a missing .gcda of a hw/vmapple object", r.returncode == 1 and "hw_vmapple_cfg" in r.stderr, r.stderr)
r = ctl("check-log", log("tcg.log", CLEAN + "[6/9] Compiling C object libqemu-aarch64-softmmu.a.p/accel_tcg_cputlb.c.o\n/x/c.c:9:1: warning: control flow checksum mismatch [-Wcoverage-mismatch]\n"))
check("check-log refuses a coverage mismatch in accel/tcg", r.returncode == 1 and "accel_tcg_cputlb" in r.stderr, r.stderr)
r = ctl("check-log", log("mm.log", CLEAN + "[6/9] Compiling C object libqemuutil.a.p/util_x.c.o\n/x/c.c:9:1: warning: number of counters mismatch [-Wcoverage-mismatch]\n"))
check("check-log refuses a coverage mismatch anywhere", r.returncode == 1, r.stderr)
r = ctl("check-log", log("fnu.log", CLEAN + "[6/9] Compiling C object libqemu-aarch64-softmmu.a.p/accel_tcg_cputlb.c.o\n/x/c.h:9:1: warning: profile for function 'h' not found in profile data [-Wmissing-profile]\n"))
check("check-log refuses a function-level miss in accel/tcg", r.returncode == 1, r.stderr)
r = ctl("check-log", log("corrupt.log", CLEAN + "[6/9] Compiling C object libqemuutil.a.p/util_x.c.o\n/x/c.c:9:1: warning: corrupted profile info: profile data is not flow-consistent [-Wcoverage-mismatch]\n"))
check("check-log refuses 'corrupted profile info' (a truncated .gcda)", r.returncode == 1, r.stderr)
r = ctl("check-log", log("corrupt2.log", CLEAN + "[6/9] Compiling C object libqemuutil.a.p/util_x.c.o\nx.gcda:1:1: warning: corrupted profile info: number of executions for edge 3-4 thought to be 5\n"))
check("check-log refuses 'corrupted profile info' without a flag name", r.returncode == 1, r.stderr)
r = ctl("check-log", os.path.join(tmp, "nonexistent.log"))
r = ctl("check-log", log("none.log", "[1/2] Compiling C object libsystem.a.p/hw_vmapple_cfg.c.o\n[2/2] Linking target qemu-system-aarch64\n"))
check("check-log refuses a log with no profile warnings at all (switched off)", r.returncode == 1, r.stderr)
check("check-log refuses a log it cannot read", r.returncode != 0)

prof = os.path.join(tmp, "prof")
os.makedirs(prof)
for n in ("libsystem.a.p#hw_vmapple_cfg.c.gcda", "libqemu-aarch64-softmmu.a.p#accel_tcg_cputlb.c.gcda"):
    open(os.path.join(prof, n), "w").write("x")
r = ctl("check-prof", prof)
check("check-prof refuses a profile without target/arm", r.returncode == 1 and "target_arm" in r.stderr, r.stderr)
open(os.path.join(prof, "libqemu-aarch64-softmmu.a.p#target_arm_tcg_translate.c.gcda"), "w").write("x")
check("check-prof accepts the three areas", ctl("check-prof", prof).returncode == 0)
check("check-prof refuses a missing directory", ctl("check-prof", os.path.join(tmp, "nope")).returncode == 1)


# ---- A4 (R29/R30 audit) regression tests that need no namespace: the functions are cut out of the CLI and run on their own
def fn_src(*names):
    """the source of top-level shell functions of the CLI (a one-line function, or from `name() {` to the next line that is `}`)"""
    lines = open(CLI).read().split("\n")
    out = []
    for n in names:
        for i, l in enumerate(lines):
            if l.startswith(n + "() {"):
                j = i
                if not l.rstrip().endswith("}"):
                    j = next(k for k in range(i + 1, len(lines)) if lines[k] == "}")
                out.append("\n".join(lines[i:j + 1]))
                break
        else:
            out.append("# missing function " + n)
    return "\n".join(out)


def sh(script, env=None, timeout=30):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=dict(os.environ, **(env or {})), timeout=timeout)


def alive(pid):
    try:
        os.kill(pid, 0)
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(") ")[-1].split()[0] != "Z"
    except OSError:
        return False


MARKED = []


def marked(marker):
    p = subprocess.Popen(["sleep", "300"], env=dict(os.environ, SNAPINTOSH_PGO_WORK=marker))
    MARKED.append(p)
    return p


def kill_marked():
    for p in MARKED:
        try:
            p.kill()
            p.wait()
        except OSError:
            pass


import atexit
atexit.register(kill_marked)


def reaped(p):
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return p.poll() is not None


# 1. the marker every process carries is the OUTER path; inside the namespace the work dir has another name. The stop must match the marker, not
#    the name of the shell that asks.
mntino = os.readlink("/proc/self/ns/mnt")
inner = os.path.join(tmp, "inner-view")
os.makedirs(inner)
open(os.path.join(inner, "mntns"), "w").write(mntino + "\n")
victim, other = marked("/outer-view/pgo-work"), marked("/somebody/elses")
r = sh(fn_src("pgo_stop_ns") + '\npgo_stop_ns "%s" /outer-view/pgo-work' % inner)
check("pgo_stop_ns stops a process by the outer marker when its own name for the work dir differs", reaped(victim) and alive(other.pid), r.stderr)
other.kill(); other.wait()
v2 = marked(inner)
r = sh(fn_src("pgo_stop_ns") + '\npgo_stop_ns "%s"' % inner)
check("pgo_stop_ns without a marker argument matches the work dir path (outside the namespace the two names agree)", reaped(v2), r.stderr)

# the watchdog: the owner is gone (no cli.pid), so it kills its target and everything marked, then ENDS (no second round, no reused-pid kill)
wd = os.path.join(tmp, "wd")
os.makedirs(wd)
open(os.path.join(wd, "mntns"), "w").write(mntino + "\n")
target, child = marked("/outer-view/wd"), marked("/outer-view/wd")
r = sh(fn_src("pgo_stop_ns", "cli_dead", "pgo_watchdog") + '\nPIDNS=x; PGO_WORK="%s"; SNAPINTOSH_PGO_WORK=/outer-view/wd; PGO_WD_TICK=0.2; ( pgo_watchdog %d ); echo ended' % (wd, target.pid), timeout=20)
check("pgo_watchdog kills its target and the marked processes of the namespace by the outer marker and ends", "ended" in r.stdout and reaped(target) and reaped(child), r.stdout + r.stderr)

# 3. the training retry starts from an empty profile directory, and the failure line names why
pw = os.path.join(tmp, "train")
os.makedirs(pw)
TRAIN = """
info() { echo "INFO $*"; }; die() { echo "DIE $*" >&2; exit 1; }
PGO_WORK="%s"
pgo_bench() { ls "$PGO_WORK/prof" | wc -l >> "$PGO_WORK/seen"; touch "$PGO_WORK/prof/$1.gcda"; echo "boom of $1" > "$PGO_WORK/$1.out"; [ "$1" = train2 ]; }
pgo_train"""
r = sh(fn_src("pgo_train") + TRAIN % pw)
check("a retried training boot starts from an empty profile directory (the failed boot's counters are dropped)",
      r.returncode == 0 and open(os.path.join(pw, "seen")).read().split() == ["0", "0"] and os.listdir(os.path.join(pw, "prof")) == ["train2.gcda"], r.stdout + r.stderr)
check("the failure of a training boot is reported with its last output line", "training boot train failed: boom of train" in r.stdout, r.stdout)

# 7 and 8. the pgo boots use free ports and the snapshots dir of their own home, never the user's
pb = os.path.join(tmp, "pb")
os.makedirs(pb)
fake = os.path.join(tmp, "fake-self.sh")
open(fake, "w").write('#!/bin/sh\nprintf "SNAPS=%s\\n" "$SNAPS"\nfor a in "$@"; do echo "ARG $a"; done\n')
r = sh(fn_src("free_port", "pgo_bench") + '\ntrack() { :; }\nPGO_WORK="%s"; SELF="%s"; SNAPS=/users/real/snaps; export SNAPS\npgo_bench w1 --foo; echo rc=$?' % (pb, fake))
out = open(os.path.join(pb, "w1.out")).read().split("\n") if os.path.exists(os.path.join(pb, "w1.out")) else []
args = [l[4:] for l in out if l.startswith("ARG ")]


def argval(k):
    return args[args.index(k) + 1] if k in args and args.index(k) + 1 < len(args) else None


check("pgo_bench runs with the snapshots dir of its own home", "SNAPS=%s/home/snaps" % pb in out, str(out) + r.stderr)
check("pgo_bench picks a free ssh port, not 10022", argval("--ssh-port") not in (None, "10022") and argval("--ssh-port").isdigit(), str(args))
check("pgo_bench picks a free page server port, not 18080", argval("--http-port") not in (None, "18080") and argval("--http-port").isdigit(), str(args))
check("pgo_bench ports differ from each other", argval("--ssh-port") != argval("--http-port"), str(args))


# ---- the flag
try:
    ns_ok = subprocess.run(["unshare", "-rm", "true"], capture_output=True).returncode == 0
except OSError:
    ns_ok = False
def cli(home, *args, env=None):
    e = dict(os.environ, SNAPINTOSH_HOME=home, SNAPINTOSH_PREFIX=os.path.join(home, "prefix"), XDG_CONFIG_HOME=os.path.join(tmp, "cfg"),
             SNAPINTOSH_REPRO_MNT=os.path.join(tmp, "mnt"), QEMU_REPO="", REIMS_REPO="", M2V_REPO="")
    e.update(env or {})
    return subprocess.run(["bash", CLI, *args], capture_output=True, text=True, env=e, timeout=120)


os.makedirs(os.path.join(tmp, "mnt"), exist_ok=True)
# the build tools the preflight asks for must exist; the fake QEMU never calls them
stubs = os.path.join(tmp, "stubs")
os.makedirs(stubs)
for t in ("meson",):
    if not shutil.which(t):
        open(os.path.join(stubs, t), "w").write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(stubs, t), 0o755)
os.environ["PATH"] = stubs + os.pathsep + os.environ["PATH"]
h1 = os.path.join(tmp, "h1")
os.makedirs(h1)
r = cli(h1, "build", "--pgo", "--only", "reims")
check("--pgo with --only reims is refused", r.returncode != 0 and "--pgo" in r.stderr, r.stderr)
r = cli(h1, "build", "--pgo", "--only", "mesa")
check("--pgo with --only mesa is refused", r.returncode != 0 and "--pgo" in r.stderr, r.stderr)
r = cli(h1, "build", "--pgo", "--only", "qemu")
check("--pgo without a plain build is refused before anything starts",
      r.returncode != 0 and "snapintosh build" in r.stderr and not os.path.exists(os.path.join(h1, "pgo-work")), r.stderr)

h2 = os.path.join(tmp, "h2")
pre = os.path.join(h2, "prefix", "bin")
os.makedirs(pre)
qbin = os.path.join(pre, "qemu-system-aarch64")
open(qbin, "w").write("#!/bin/sh\necho working binary\n")
os.chmod(qbin, 0o755)
want = hashlib.md5(open(qbin, "rb").read()).hexdigest()
r = cli(h2, "build", "--pgo", "--only", "qemu")
check("--pgo without a golden is refused and names make-disk golden", r.returncode != 0 and "make-disk golden" in r.stderr, r.stderr)
os.makedirs(os.path.join(h2, "vm", "golden"))
open(os.path.join(h2, "vm", "golden", "root-quiet.qcow2"), "w").write("x")
dist = os.path.join(h2, "build", "reims-target", "dist-vulkan", "lib", "pkgconfig")
os.makedirs(dist)
open(os.path.join(dist, "reims-vgpu.pc"), "w").write("x")
r = cli(h2, "build", "--pgo", "--only", "qemu", env={"SNAPINTOSH_DRY": "1"})
if ns_ok:
    check("--pgo dry run prints the expected time and the steps",
          r.returncode == 0 and "50 minutes" in r.stdout and "6 cores" in r.stdout and "4 h" in r.stdout and "train" in r.stdout and not os.path.exists(os.path.join(h2, "pgo-work")),
          r.stdout + r.stderr)
else:
    check("--pgo without user namespaces is refused and says why", r.returncode != 0 and "user namespaces" in r.stderr, r.stderr)

# 6. AppArmor's user namespace restriction: said up front, with the exact fix, in the refusal and in check-host. A stub unshare stands for a host
#    that refuses (the stub works on any host), the sysctl file is the one the CLI reads (SNAPINTOSH_APPARMOR_USERNS).
bad_unshare = os.path.join(tmp, "unshare-bad")
good_unshare = os.path.join(tmp, "unshare-good")
for d, body in ((bad_unshare, '#!/bin/sh\necho "unshare: write failed /proc/self/uid_map: Operation not permitted" >&2\nexit 1\n'),
                (good_unshare, '#!/bin/sh\nif [ "$1 $2" = "-rm true" ]; then exit 0; fi\nexec %s "$@"\n' % shutil.which("unshare"))):
    os.makedirs(d)
    open(os.path.join(d, "unshare"), "w").write(body)
    os.chmod(os.path.join(d, "unshare"), 0o755)
aa = os.path.join(tmp, "aa-userns")
open(aa, "w").write("1\n")
penv = lambda d: {"PATH": d + os.pathsep + os.environ["PATH"], "SNAPINTOSH_APPARMOR_USERNS": aa}
r = cli(h2, "build", "--pgo", "--only", "qemu", env=penv(bad_unshare))
check("--pgo on an AppArmor-restricted host says so and prints the sysctl fix",
      r.returncode != 0 and "kernel.apparmor_restrict_unprivileged_userns=0" in r.stderr and "sudo sysctl" in r.stderr, r.stderr)
check("--pgo on an AppArmor-restricted host prints the profile fix", "apparmor_parser" in r.stderr and "userns," in r.stderr, r.stderr)
r = cli(h2, "check-host", env=dict(penv(bad_unshare), SNAPINTOSH_CHECK_THREADS="8"))
check("check-host says build --pgo cannot run here and how to fix it", "build --pgo cannot run here" in r.stdout and "kernel.apparmor_restrict_unprivileged_userns=0" in r.stdout, r.stdout[-600:])
r = cli(h2, "check-host", env=dict(penv(good_unshare), SNAPINTOSH_CHECK_THREADS="8"))
check("check-host says build --pgo can run where user namespaces work", "build --pgo can run here" in r.stdout and "build --pgo cannot run" not in r.stdout, r.stdout[-600:])

# 4. a --pgo that dies before it starts anything (no QEMU_REPO, pin not fetchable) leaves nothing and does not block the next try
home4 = os.path.join(tmp, "h4")
shutil.copytree(h2, home4, symlinks=True)
r1 = cli(home4, "build", "--pgo", "--only", "qemu", env=penv(good_unshare))
left4 = [n for n in os.listdir(home4) if n.startswith("pgo-work")]
check("--pgo that dies in clone_pin names the cause and leaves no work dir", r1.returncode != 0 and "QEMU_REPO is not set" in r1.stderr and not left4, "%s %s" % (left4, r1.stderr[-300:]))
r2 = cli(home4, "build", "--pgo", "--only", "qemu", env=penv(good_unshare))
check("the next --pgo try right after is not told that another build runs", "another build --pgo" not in r2.stderr and "QEMU_REPO is not set" in r2.stderr, r2.stderr[-300:])

# 9. an install that was cut short leaves *.pgo-new copies: the next command removes them with the dead build's work dir
home9 = os.path.join(tmp, "h9")
shutil.copytree(h2, home9, symlinks=True)
pidns = os.readlink("/proc/self/ns/pid")[len("pid:["):-1]
gone = subprocess.Popen(["true"]); gone.wait()
os.makedirs(os.path.join(home9, "pgo-work"))
open(os.path.join(home9, "pgo-work", "cli.pid"), "w").write("%d:1:%s\n" % (gone.pid, pidns))
os.makedirs(os.path.join(home9, "pgo-work.new-%d" % gone.pid))
open(os.path.join(home9, "pgo-work.new-%d" % gone.pid, "cli.pid"), "w").write("%d:1:%s\n" % (gone.pid, pidns))
open(os.path.join(home9, "prefix", "bin", "qemu-system-aarch64.pgo-new"), "w").write("x")
os.makedirs(os.path.join(home9, "prefix", "share", "snapintosh"))
open(os.path.join(home9, "prefix", "share", "snapintosh", "pgo.txt.new"), "w").write("x")
r = cli(home9, "build", "--pgo", "--only", "reims")
check("a command after a dead build removes its work dir, its private dir and the .pgo-new copies",
      not any(n.startswith("pgo-work") for n in os.listdir(home9)) and not os.path.exists(os.path.join(home9, "prefix", "bin", "qemu-system-aarch64.pgo-new"))
      and not os.path.exists(os.path.join(home9, "prefix", "share", "snapintosh", "pgo.txt.new")), "%s %s" % (os.listdir(home9), r.stderr[-300:]))
src = open(CLI).read()
ix = [src.find(x) for x in ('[ "$rel" != bin/qemu-system-aarch64 ] || continue', 'mv -f "$PREFIX/bin/qemu-system-aarch64.pgo-new"', 'mv -f "$PREFIX/share/snapintosh/pgo.txt.new"')]
check("the install writes the QEMU binary last, then pgo.txt", 0 <= ix[0] < ix[1] < ix[2], ix)
bp = src[src.index("build_qemu_pgo() {"):]
check("the pgo lock is taken before clone_pin touches the shared source", 0 <= bp.find('mv -T "$tmpwork"') < bp.find("clone_pin QEMU"), "")
check("a failed copy during the install stops the build instead of going on", src.count('|| die "build --pgo: installing') == 2, "")
r = cli(h2, "build", "--pgo", "--only", "qemu", env=dict(penv(bad_unshare), SNAPINTOSH_NO_REPRO="1"))
check("--pgo with SNAPINTOSH_NO_REPRO=1 names that cause, not AppArmor", r.returncode != 0 and "SNAPINTOSH_NO_REPRO" in r.stderr and "apparmor_restrict" not in r.stderr, r.stderr)
check("--pgo is listed in the usage", "--pgo" in subprocess.run(["bash", CLI, "--help"], capture_output=True, text=True).stdout)

# ---- clean-up after a kill: a fake QEMU repo whose configure stays alive, so the build sits in the instrumented step
repo = os.path.join(tmp, "qemu-fake")
os.makedirs(os.path.join(repo, "scripts", "27on86"))
subprocess.run(["git", "init", "-q", repo], check=True)
open(os.path.join(repo, "configure"), "w").write('#!/bin/sh\necho started > "$PWD/configure.started"\nsleep 300 &\nwait\n')
os.chmod(os.path.join(repo, "configure"), 0o755)
open(os.path.join(repo, "scripts", "27on86", "x"), "w").write("x")
g = ["git", "-C", repo, "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t"]
subprocess.run(g + ["add", "-A"], check=True)
subprocess.run(g + ["commit", "-qm", "init"], check=True)
sha = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def leftover():
    """processes that carry a work dir of this test in their environment (the build marks everything it starts)"""
    out = []
    for p in os.listdir("/proc"):
        if p.isdigit() and int(p) != os.getpid():
            try:
                env = open("/proc/%s/environ" % p, "rb").read()
            except OSError:
                continue
            if b"SNAPINTOSH_PGO_WORK=" + tmp.encode() in env:
                out.append(p)
    return out


def start(home, fail=False):
    e = dict(os.environ, SNAPINTOSH_HOME=home, SNAPINTOSH_PREFIX=os.path.join(home, "prefix"), XDG_CONFIG_HOME=os.path.join(tmp, "cfg"),
             SNAPINTOSH_REPRO_MNT=os.path.join(tmp, "mnt"), QEMU_REPO="file://" + repo, QEMU_REF=sha, REIMS_REPO="", M2V_REPO="")
    return subprocess.Popen(["bash", CLI, "build", "--pgo", "--only", "qemu"], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_for(pred, secs=60):
    t = time.time()
    while time.time() - t < secs:
        if pred():
            return True
        time.sleep(0.3)
    return False


def started(home):
    for root, _, files in os.walk(os.path.join(home, "pgo-work")):
        if "configure.started" in files:
            return True
    return False


if not ns_ok:
    print("skip  user namespaces are not available: kill clean-up tests not run")
else:
    for sig, label in ((signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")):
        home = os.path.join(tmp, "k" + label)
        shutil.copytree(h2, home, symlinks=True)
        p = start(home)
        check("pgo build %s: reached the instrumented build in the namespace" % label, wait_for(lambda: started(home), 90))
        p.send_signal(sig)
        try:
            p.wait(timeout=60)
            ended = True
        except subprocess.TimeoutExpired:
            p.kill()
            ended = False
        out = p.stdout.read()
        check("pgo build ended by %s: exits non-zero" % label, ended and p.returncode != 0, out[-400:])
        check("pgo build ended by %s: no work dir left" % label, not os.path.exists(os.path.join(home, "pgo-work")), str(os.listdir(home)))
        check("pgo build ended by %s: no process left" % label, wait_for(lambda: not leftover(), 10), str(leftover()))
        check("pgo build ended by %s: the working binary is untouched" % label,
              hashlib.md5(open(os.path.join(home, "prefix/bin/qemu-system-aarch64"), "rb").read()).hexdigest() == want)

    # a build that fails on its own leaves nothing and says so
    open(os.path.join(repo, "configure"), "w").write("#!/bin/sh\nexit 1\n")
    subprocess.run(g + ["commit", "-qam", "fail"], check=True)
    sha2 = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    home = os.path.join(tmp, "kFAIL")
    shutil.copytree(h2, home, symlinks=True)
    r = cli(home, "build", "--pgo", "--only", "qemu", env={"QEMU_REPO": "file://" + repo, "QEMU_REF": sha2})
    check("a failing pgo build exits non-zero and removes its work dir", r.returncode != 0 and not os.path.exists(os.path.join(home, "pgo-work")), r.stdout + r.stderr)
    check("a failing pgo build leaves the working binary", hashlib.md5(open(os.path.join(home, "prefix/bin/qemu-system-aarch64"), "rb").read()).hexdigest() == want)

    # A4 #1: SIGKILL of the CLI and no further command: the watchdog inside the namespace ends everything the build started within seconds
    home = os.path.join(tmp, "kWD")
    shutil.copytree(h2, home, symlinks=True)
    p = start(home)
    check("pgo build SIGKILL (watchdog): reached the instrumented build", wait_for(lambda: started(home), 90))
    p.kill()
    p.wait()
    p.stdout.close()
    check("pgo build SIGKILL (watchdog): nothing it started runs after 20 s, without another snapintosh command", wait_for(lambda: not leftover(), 20), str(leftover()))

    # SIGKILL: nothing can run in the CLI, the next command finds the dead owner and cleans up
    home = os.path.join(tmp, "kKILL")
    shutil.copytree(h2, home, symlinks=True)
    p = start(home)
    check("pgo build SIGKILL: reached the instrumented build", wait_for(lambda: started(home), 90))
    p.kill()
    p.wait()
    p.stdout.close()   # the namespace it left still holds the pipe: reading would wait for it
    check("pgo build SIGKILL: the work dir is still there (nothing ran)", os.path.exists(os.path.join(home, "pgo-work")))
    r = cli(home, "build", "--pgo", "--only", "reims")
    check("next build command removes the dead build's work dir", not os.path.exists(os.path.join(home, "pgo-work")) and "pgo" in r.stderr, r.stderr)
    check("next build command stops what it left running", wait_for(lambda: not leftover(), 10), str(leftover()))
    check("pgo build SIGKILL: the working binary is untouched",
          hashlib.md5(open(os.path.join(home, "prefix/bin/qemu-system-aarch64"), "rb").read()).hexdigest() == want)

shutil.rmtree(tmp, ignore_errors=True)
print("all passed" if not fails else "%d failed" % fails)
sys.exit(1 if fails else 0)
