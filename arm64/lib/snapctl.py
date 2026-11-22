#!/usr/bin/env python3
"""snapctl.py: save and resume helper for `snapintosh snapshot` and `resume`.

  snapctl.py check NAME QEMU CPUS RAM EXTRA GPU            exit 3 with a message unless NAME can be resumed by QEMU
  snapctl.py pick NAME QEMU                                print the binary that resumes NAME (QEMU, or a kept older build), exit 3 if none
  snapctl.py keep QEMU FORMAT                              before QEMU is replaced: keep it next to itself if a snapshot still needs it
  snapctl.py save NAME RUNDIR QEMU CPUS RAM EXTRA GPU PORT KEY SETTLE [PRE]
                                                           wait for ssh, settle, save the running guest, quit QEMU
  snapctl.py incoming NAME RUNDIR                          load the stream into a QEMU started with -incoming defer, cont
  snapctl.py finish NAME RUNDIR QEMU CPUS RAM EXTRA GPU    after QEMU and nbd exited: copy the disks, write meta.json, move the snapshot into place
  snapctl.py abort NAME RUNDIR                             drop the staging directory of a save that failed
  snapctl.py settime RUNDIR PORT KEY [LIMIT [BUDGET]]      after a resume: step the guest clock

Snapshots live in $SNAPS (default $SNAPINTOSH_HOME/snaps, else ~/.local/share/snapintosh/snaps)/NAME: vm.state, aux, root.qcow2, meta.json. meta.json is written
last and holds the md5 of the QEMU binary and its state format. A snapshot is loaded by the binary that wrote it, or by any
build with the same state format (the QEMU fork's vmstate plus the renderer's suspend blob, pinned as SNAPSHOT_STATE_FORMAT in
pins.env and installed as share/snapintosh/state-format; relinking the renderer with a bug fix changes the md5 but not the
format). When the format changed, `pick` finds the older binary that `keep` saved next to the new one."""
import glob, hashlib, json, os, re, select, shlex, shutil, socket, subprocess, sys, time

def default_home():
    return os.environ.get("SNAPINTOSH_HOME") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "snapintosh")


SNAPS = os.environ.get("SNAPS") or os.path.join(default_home(), "snaps")
FMT = os.environ.get("SNAPFMT", "auto")   # auto | zstd | mapped (mapped-ram + multifd, uncompressed)
CHANNELS = int(os.environ.get("SNAPCHANNELS", "8"))


NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def valid_name(name):
    """A snapshot name is a directory name under SNAPS and ends up in shell strings: letters, digits, dot, underscore and dash,
    1 to 64 characters, no leading dot or dash, and no `..` anywhere (so never a path, never hidden, never an option)."""
    return isinstance(name, str) and NAME_RE.fullmatch(name) is not None and ".." not in name


def sdir(name):
    """SNAPS/NAME for a valid name; anything else ends the process with exit 2 before a path is built from it"""
    if not valid_name(name):
        print("snapintosh: invalid snapshot name %r: use 1 to 64 of A-Z a-z 0-9 . _ - (first one a letter or digit, no '..')" % (name,),
              file=sys.stderr)
        sys.exit(2)
    return os.path.join(SNAPS, name)


def stage_dir(name, rundir):
    """where a save is built: a hidden directory in SNAPS (same file system, so the final rename is atomic), unique per run"""
    sdir(name)
    return os.path.join(SNAPS, ".stage-%s-%s" % (name, os.path.basename(os.path.normpath(rundir))))


def zstd_write_uri(state):
    """the migrate URI that pipes the stream into zstd: QEMU runs everything after `exec:` through /bin/sh, so quote the path"""
    return "exec:" + shlex.join(["zstd", "-T0", "-1", "-q", "-f", "-o", state])


def zstd_read_uri(state):
    return "exec:" + shlex.join(["zstd", "-d", "-T0", "-q", "-c", state])


def writer_running(state):
    """is a zstd still writing STATE? Looks at /proc, no pattern matching (a path is not a regex)"""
    want = state.encode()
    me = str(os.getpid())
    for p in os.listdir("/proc"):
        if not p.isdigit() or p == me:
            continue
        try:
            c = open("/proc/%s/cmdline" % p, "rb").read()
        except OSError:
            continue
        if b"zstd" in c and want in c:
            return True
    return False


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def state_format(qemu):
    """The state format of the build installed as QEMU (prefix/bin/..): the file `build` writes, "" if there is none."""
    try:
        return open(os.path.join(os.path.dirname(os.path.abspath(qemu)), "..", "share", "snapintosh", "state-format")).read().strip()
    except OSError:
        return ""


def kept_binary(qemu, want_md5):
    """A binary `keep` saved beside QEMU for this md5, or None."""
    k = "%s.%s" % (os.path.abspath(qemu), want_md5[:8])
    return k if os.path.isfile(k) and md5(k) == want_md5 else None


def meta_for(qemu, cpus, ram, extra, gpu):
    return {"binary": os.path.realpath(qemu), "binary_md5": md5(qemu), "state_format": state_format(qemu), "cpus": int(cpus),
            "ram": ram, "extra": extra, "gpu": int(gpu)}


def compatible(m, cur):
    """Same binary, or the same non-empty state format (an older snapshot without the field only matches its own md5)."""
    return m["binary_md5"] == cur["binary_md5"] or (cur["state_format"] != "" and m.get("state_format", "") == cur["state_format"])


def mismatch_text(name, m, cur, qemu):
    k = kept_binary(qemu, m["binary_md5"])
    use = ("it is kept at %s: snapintosh resume uses it by itself" % k) if k else \
          "that binary is not kept: restore a copy of the QEMU with md5 %s (or rebuild the pins it came from) and resume with SNAPINTOSH_PREFIX pointing at its prefix" % m["binary_md5"]
    return ("RESUME refused: snapshot '%s' was saved by QEMU md5 %s, state format %s; this run uses md5 %s, state format %s, "
            "and the device state differs between them. %s" % (name, m["binary_md5"], m.get("state_format") or "unrecorded",
                                                               cur["binary_md5"], cur["state_format"] or "unrecorded", use))


def pick(name, qemu):
    try:
        m = json.load(open(sdir(name) + "/meta.json"))
    except Exception:
        print("RESUME refused: no complete snapshot '%s' in %s (meta.json missing). Snapshots made by an older version may be in another "
              "directory: the default is $SNAPINTOSH_HOME/snaps, and SNAPS=DIR points the command at the old one." % (name, SNAPS), file=sys.stderr)
        return 3
    cur = {"binary_md5": md5(qemu), "state_format": state_format(qemu)}
    if compatible(m, cur):
        if m["binary_md5"] != cur["binary_md5"]:
            print("snapintosh: snapshot '%s' was saved by QEMU md5 %s; this build (md5 %s) has the same state format %s, resuming with it"
                  % (name, m["binary_md5"][:8], cur["binary_md5"][:8], cur["state_format"]), file=sys.stderr)
        print(qemu)
        return 0
    k = kept_binary(qemu, m["binary_md5"])
    if k:
        print("snapintosh: snapshot '%s' needs the older QEMU md5 %s (state format changed), using %s" % (name, m["binary_md5"][:8], k),
              file=sys.stderr)
        print(k)
        return 0
    print(mismatch_text(name, m, cur, qemu), file=sys.stderr)
    return 3


def keep(qemu, new_format):
    """Called by `build` before the binary at QEMU is overwritten: if a snapshot saved by it cannot be resumed by a build with
    state format NEW_FORMAT, keep a copy as QEMU.<md5 first 8>. A copy is 0.4 GB, made only when a snapshot needs it."""
    if not os.path.isfile(qemu):
        return 0
    old = md5(qemu)
    need = []
    for mp in sorted(glob.glob(SNAPS + "/*/meta.json")):
        try:
            m = json.load(open(mp))
        except Exception:
            continue
        if m.get("binary_md5") == old and (not new_format or m.get("state_format", "") != new_format):
            need.append(os.path.basename(os.path.dirname(mp)))
    if need and not kept_binary(qemu, old):
        shutil.copy2(qemu, "%s.%s" % (os.path.abspath(qemu), old[:8]))
        print("snapintosh: kept the old QEMU as %s.%s for the snapshots %s (their device state is not readable by the new build)"
              % (qemu, old[:8], ", ".join(need)))
    return 0


def check(name, qemu, cpus, ram, extra, gpu):
    d = sdir(name)
    try:
        m = json.load(open(d + "/meta.json"))
    except Exception:
        print("RESUME refused: no complete snapshot '%s' in %s (meta.json missing). Snapshots made by an older version may be in another "
              "directory: the default is $SNAPINTOSH_HOME/snaps, and SNAPS=DIR points the command at the old one." % (name, SNAPS), file=sys.stderr)
        return 3
    cur = meta_for(qemu, cpus, ram, extra, gpu)
    if not compatible(m, cur):
        print(mismatch_text(name, m, cur, qemu), file=sys.stderr)
        return 3
    for k in ("cpus", "ram", "extra", "gpu"):
        if m[k] != cur[k]:
            print("RESUME refused: snapshot '%s' was saved with %s=%r, this run has %r" % (name, k, m[k], cur[k]),
                  file=sys.stderr)
            return 3
    return 0


class Qmp:
    def __init__(self, path, timeout=None):
        if timeout is None:
            timeout = float(os.environ.get("SNAPCTL_QMP_TIMEOUT", "120"))
        t = time.time()
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX); self.s.connect(path); break
            except OSError:
                if time.time() - t > timeout: raise
                time.sleep(0.2)
        self.f = self.s.makefile("rw"); self.f.readline(); self.cmd("qmp_capabilities")

    def cmd(self, name, **a):
        self.f.write(json.dumps({"execute": name, "arguments": a}) + "\n"); self.f.flush()
        while True:
            d = json.loads(self.f.readline())
            if "return" in d: return d["return"]
            if "error" in d: raise RuntimeError("%s: %s" % (name, d["error"]))


def ram_bytes(ram):
    n = int(ram[:-1]) if ram[-1:].upper() in "GM" else int(ram)
    return n * {"G": 1 << 30, "M": 1 << 20}.get(ram[-1:].upper(), 1)


def pick_format(ram):
    """mapped-ram + multifd restores about 4 times faster than zstd but writes the whole RAM size (an 8 GiB guest
    is 8.3 GB on disk against 2.8 GB): used when the disk has the room, else zstd"""
    global FMT
    if FMT == "auto":
        st = os.statvfs(SNAPS)
        FMT = "mapped" if st.f_bavail * st.f_frsize > ram_bytes(ram) * 1.15 + (2 << 30) else "zstd"
    return FMT


def caps(q):
    """mapped-ram and multifd on for a mapped save, explicitly off for zstd (a VM that was loaded mapped keeps them on)"""
    on = FMT == "mapped"
    q.cmd("migrate-set-capabilities", capabilities=[{"capability": "mapped-ram", "state": on},
                                                    {"capability": "multifd", "state": on}])
    if on:
        q.cmd("migrate-set-parameters", **{"multifd-channels": CHANNELS})


# the guest side of GuestClock: a line "N" or "N S SEC USEC" (N a sequence number). The second form sets the clock first
# (settimeofday, SYS 122 on macOS). The answer is "N TIME RESULT", RESULT "-" when no step was asked.
GUEST_CLOCK_LOOP = ("perl -MTime::HiRes=time -e '$|=1;while(<STDIN>){$r=q(-);/^(\\d+)/;$n=$1;if(/^\\d+ S (\\d+) (\\d+)/){$r=syscall(122,pack(q(ql x4),$1,$2),0)}"
                    "printf qq(%s %.6f %s\\n),$n,time,$r}'")


class GuestClock:
    """A guest clock reader that costs one line per sample. Spawning `perl` over ssh for every sample (g_read) costs a login
    shell, an exec and a session teardown, which take seconds while the guest catches up after a resume (load average 500 on
    6 threads) and 0.7 s when it is idle; the guest reads its clock somewhere in that time, so the sample is biased by up to half of it
    (spawn rtt 1.3 s read +0.65 s while a line to a running perl, rtt 6 ms, read -0.005 s at the same moment: the clock was right and
    the old check called it off). Here one perl runs in the guest for the whole check and answers each line with its time.
    sample() returns (rtt, guest minus host) or None; step(host_time) sets the guest clock with settimeofday (sub-second)."""

    def __init__(self, ssh):
        self.p = subprocess.Popen(ssh + [GUEST_CLOCK_LOOP], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.seq = 0
        self.buf = b""

    def ask(self, what, timeout):
        """send one numbered line, return (t_sent, t_answered, [time, result]) or None when its answer does not come in timeout
        seconds. Answers to earlier lines (a sample that timed out) carry another number and are skipped, so a late answer is never
        taken for this one."""
        fd = self.p.stdout.fileno()
        self.seq += 1
        try:
            t0 = time.time()
            self.p.stdin.write(b"%d%s\n" % (self.seq, what))
            while True:
                while b"\n" in self.buf:
                    line, self.buf = self.buf.split(b"\n", 1)
                    w = line.split()
                    if len(w) == 3 and w[0] == b"%d" % self.seq:
                        return t0, time.time(), w[1:]
                left = t0 + timeout - time.time()
                if left <= 0 or not select.select([fd], [], [], left)[0]:
                    return None
                chunk = os.read(fd, 256)
                if not chunk:
                    return None
                self.buf += chunk
        except (OSError, ValueError):
            return None

    def sample(self, timeout=10.0):
        a = self.ask(b"", timeout)
        try:
            return (a[1] - a[0], float(a[2][0]) - (a[0] + a[1]) / 2) if a else None
        except (ValueError, IndexError):
            return None

    def step(self, host_time, timeout=10.0):
        """set the guest clock to host_time (host epoch seconds) as it runs; True when settimeofday answered 0. The caller adds the
        half round trip it measured and reads the clock again to see what is left."""
        a = self.ask(b" S %d %d" % (int(host_time), int((host_time % 1) * 1e6)), timeout)
        return bool(a) and a[2][1] == b"0"

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(2)
        except subprocess.TimeoutExpired:
            self.p.kill(); self.p.wait()


def measure(sample, rtt_max, deadline, want=3, pause=0.25, log=None):
    """The reading to trust: the sample with the shortest round trip among WANT whose round trip is at most rtt_max (the error of a
    sample is at most half its round trip, so a long one says nothing about a limit of rtt_max s). Samples with a longer round trip are
    refused and sampling goes on until the deadline, because the guest gets quicker as it settles. (rtt, off) or None."""
    good = []
    while time.time() < deadline and len(good) < want:
        b = sample()
        if log:
            log(b)
        if b is not None and b[0] <= rtt_max:
            good.append(b)
        else:
            time.sleep(pause)
    return min(good) if good else None


def within(b, limit):
    """is the guest clock within limit seconds for sure: the offset plus the half round trip that the reading cannot resolve"""
    return abs(b[1]) + b[0] / 2 <= limit


def g_read(ssh, n=3, rtt_cap=0.6):
    """guest minus host in seconds, hires (perl Time::HiRes in the guest), one spawned perl per sample: for a guest that is not
    busy (the save, before the settle). Keeps the sample with the shortest round trip; takes up to 4n samples until one has a round
    trip of rtt_cap seconds or less (the error of a sample is at most half its round trip)."""
    best = None
    for i in range(4 * n):
        if i >= n and best is not None and best[0] <= rtt_cap:
            break
        t0 = time.time()
        g = subprocess.run(ssh + ["perl -MTime::HiRes=time -e 'printf q(%.3f),time'"], capture_output=True, text=True).stdout.strip()
        t1 = time.time()
        try:
            gv = float(g)
        except ValueError:
            continue
        if best is None or t1 - t0 < best[0]:
            best = (t1 - t0, gv - (t0 + t1) / 2)
        if best[0] <= rtt_cap and i + 1 >= 2:
            break
    return best


def g_step(ssh, lat):
    """one `date` step. date sets whole seconds, so the error is S minus the host time at which the guest runs it. The command is
    sent lat seconds before the host second S begins (lat = the send-to-run delay, learned from the last try), so the guest runs
    it at about S. Returns the host time at which it was sent."""
    S = int(time.time() + lat) + 1
    t_send = S - lat
    time.sleep(max(0.0, t_send - time.time()))
    try:
        subprocess.run(ssh + ["date -u " + time.strftime("%m%d%H%M%Y.%S", time.gmtime(S))], capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        pass
    return S, t_send


def clock_step(ssh, tries=4, want=0.4):
    """put the guest clock on host time before a save (a guest that was never stepped is some seconds off, and the counter
    advance of the next resume carries that error). Readings and the step go through one running perl in the guest (GuestClock);
    only when that gives nothing at all, spawned `date` steps (g_step) with the delay learned from the reading before."""
    gc = GuestClock(ssh)
    try:
        best = None
        for _ in range(tries):
            b = measure(gc.sample, want, time.time() + 15, want=2)
            if b is None:
                break
            best = b
            if within(b, want):
                return best
            if not gc.step(time.time() + b[0] / 2):
                best = None             # no settimeofday in this guest: spawned `date` steps below
                break
        if best is not None:
            return best
    finally:
        gc.close()
    lat = 0.4
    for _ in range(tries):
        S, t_send = g_step(ssh, lat)
        time.sleep(0.5)
        b = g_read(ssh, 3)
        if not b:
            continue
        best = b
        lat = min(max((S - b[1]) - t_send, 0.0), 4.0)     # guest ran the step at host time S - diff
        if abs(b[1]) <= want:
            break
    return best


def parse_ppm(data):
    """(width, height, pixel bytes) of a P6 ppm as QEMU's screendump writes it"""
    parts = data.split(b"\n", 3)
    w, h = map(int, parts[1].split())
    return w, h, parts[3]


def colour_counts(w, h, px):
    """(full, edge). full: distinct colours of a sparse sample of the whole screen (a good desktop 1100 to 1500, a black
    wallpaper under the Dock and widgets about 100). edge: distinct colours of the two side columns (2.5 % wide each, between
    the menu bar and the Dock), the larger of the two. a Safari window covering most of the screen leaves full at 317 to 365
    although the desktop is drawn, but the side columns stay uncovered, show only wallpaper (5000 to 11000 colours drawn, 1 to
    23 black) and the Dock, widgets and windows never reach them."""
    full = len(set(px[i:i + 3] for i in range(0, len(px), 3 * 997)))
    ew = max(2, int(w * 0.025))
    edge = 0
    for x0 in (0, w - ew):
        seen = set()
        for y in range(int(h * 0.04), int(h * 0.90), 2):
            o = (y * w + x0) * 3
            row = px[o:o + 3 * ew]
            seen.update(row[i:i + 3] for i in range(0, len(row), 3))
        edge = max(edge, len(seen))
    return full, edge


EDGE_MIN = 300      # drawn wallpaper at the side columns: 2679 and up; black 23 and below (tests/test_wallpaper.py)
FULL_MIN = 500


def drawn(counts):
    """the desktop is drawn when the sparse whole-screen sample is rich (no big window) or the side columns show the wallpaper
    (a big window hides most of the screen but never the side columns)"""
    return counts is not None and (counts[0] >= FULL_MIN or counts[1] >= EDGE_MIN)


def screen_dump(q, path):
    """the screen as (width, height, pixel bytes), None when the dump failed"""
    try:
        if os.path.exists(path):
            os.remove(path)
        q.cmd("screendump", filename=path, format="ppm")
        for _ in range(50):
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                break
            time.sleep(0.2)
        time.sleep(0.4)
        return parse_ppm(open(path, "rb").read())
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def screen_colours(q, path):
    """(full, edge) colour counts of a screendump (see colour_counts), None when the dump failed."""
    d = screen_dump(q, path)
    return colour_counts(*d) if d else None


def crash_page(w, h, px):
    """A live soak showed: WebKit's web-process error page ("A problem repeatedly occurred" with a Reload button, left
    by the GPU watchdog loop) has a window, a stable page process and a quiet guest, so only the picture tells it from a page.
    Signature (tools/q1/scan.py, 1440x1080, scaled to any size): the middle of the window is one flat light grey (246 +-6) over more than
    80 percent, and the Reload button's blue (r < 150, 120 < g < 210, b > 235) sits at the centre of the screen, more than 100 of 3200
    sampled pixels there."""
    if not px or len(px) < w * h * 3:
        return False
    def at(x, y):
        o = (min(y, h - 1) * w + min(x, w - 1)) * 3
        return px[o], px[o + 1], px[o + 2]
    gray = tot = 0
    for j in range(30, 110):
        for i in range(40, 140):
            r, g, b = at(i * w // 180, j * h // 135); tot += 1
            if abs(r - 246) < 6 and abs(g - 246) < 6 and abs(b - 246) < 6:
                gray += 1
    blue = 0
    for j in range(32):
        for i in range(100):
            r, g, b = at(int(w * (620 + 2 * i) / 1440), int(h * (545 + 2.03 * j) / 1080))
            if b > 235 and r < 150 and 120 < g < 210:
                blue += 1
    return gray / tot > 0.8 and blue > 100


APP_REFUSED = 5   # exit status of a save that refused because the app was not on screen


def save_app_options():
    """(app, required, url) from the environment the CLI sets (--app NAME, --open URL): the app whose window must be on screen at the save,
    whether a miss refuses the save (an explicit --app or --open) or only warns (Safari found running by itself), and the page to open"""
    app = os.environ.get("SAVE_APP", "")
    url = os.environ.get("SAVE_OPEN_URL", "")
    if url and not app:
        app = "Safari"
    return (app or "Safari"), bool(app), url


def open_in_guest(ssh, app, url):
    """--open URL: wait (bounded) for the Dock and Finder, then open the page through the one robust helper (lib/guestapp.py)"""
    ga = guestapp()
    end = time.time() + float(os.environ.get("SAVE_DESK_WAIT", "300"))
    print("SAVE: --open: waiting for the Dock and Finder before opening %s in %s (at most %d s)" % (url, app, end - time.time()), flush=True)
    while time.time() < end:
        rc, out = ga.remote(ssh, "pgrep -x Dock >/dev/null && pgrep -x Finder >/dev/null && echo desk", 30)
        if "desk" in out:
            break
        time.sleep(3)
    r = ga.open_app(ssh, app, url, verify_s=float(os.environ.get("SAVE_OPEN_VERIFY", "60")), log=lambda m: print("SAVE: " + m, flush=True))
    if not r["ok"]:
        print("SAVE: warning: %s did not start after %d tries; the gate below decides whether the save goes on" % (app, r["tries"]), flush=True)


def app_gate(ssh, app, required, q=None, rundir=None, reopen=None):
    """do not save a snapshot of an app that never drew. When APP is running (or --app / --open named it) its window
    must be on screen, and for Safari its page process must have stayed up; wait for that at most APPWAIT seconds (default 180). A Safari that shows WebKit's error page (picture check, crash_page) is not on screen either; with --open the page is opened again.
    A miss refuses the save (returns APP_REFUSED, nothing is written, an older snapshot stays) when the app was named, else warns.
    A guest that cannot say (osascript failing) is reported and never blocks. APPWAIT=0 turns the check off."""
    limit = float(os.environ.get("APPWAIT", "180"))
    if limit <= 0:
        return 0
    ga = guestapp()
    if not required and not ga.is_running(ssh, app):
        return 0
    t0 = time.time(); last = t0; first_ready = None; last_reopen = 0.0
    answered = None   # the last "ready" / "not-ready" answer of the whole window: the verdict comes from all polls, never only the last one
    stable = float(os.environ.get("APPSTABLE", "20"))
    print("SAVE: checking that %s is on screen before the save (at most %d s, APPWAIT; it must stay ready for %d s, APPSTABLE)" % (app, limit, stable), flush=True)
    while True:
        state, detail = ga.ready(ssh, app)
        if state == "ready" and q is not None and app == "Safari":
            d = screen_dump(q, rundir + "/gate.ppm")
            if d and crash_page(*d):
                state, detail = "not-ready", "Safari shows its 'A problem repeatedly occurred' page"
                if reopen and time.time() - last_reopen >= 45:
                    last_reopen = time.time()
                    print("SAVE: Safari shows the error page left by the WebKit watchdog loop; opening the page again", flush=True)
                    reopen()
        if state != "unknown":
            answered = detail
        if state == "ready":
            # seen on the guest: a ready moment between two restarts of the page process; only a state that holds counts
            first_ready = first_ready or time.time()
            if time.time() - first_ready >= stable:
                print("SAVE: %s is on screen (%s) after %d s, ready for %d s" % (app, detail, time.time() - t0, time.time() - first_ready), flush=True)
                return 0
            detail = "ready for %d s so far" % (time.time() - first_ready)
        else:
            first_ready = None
        if time.time() - t0 >= limit:
            if state == "unknown" and answered is None:   # never got an answer in all that time: the guest cannot say, so the check cannot refuse
                print("SAVE: warning: %s; saving without knowing whether it drew" % detail, flush=True)
                return 0
            if state == "unknown":   # it answered earlier (not on screen) and only the last poll timed out: that is not a pass
                detail = "%s; the last check got no answer" % answered
            break
        if time.time() - last >= 30:
            last = time.time()
            print("SAVE: still waiting for %s, %d s so far (%s)" % (app, last - t0, detail), flush=True)
        time.sleep(5)
    if required:
        print("SAVE refused: %s is not on screen after %d s (%s), so the snapshot would hold a page that never loaded. Nothing was saved and an older "
              "snapshot of this name is untouched. Open the page again, or give it more time (--settle, APPWAIT=SECONDS); on a 6 thread host "
              "Safari often needs several minutes (README, known issue on slow hosts)." % (app, limit, detail), file=sys.stderr, flush=True)
        return APP_REFUSED
    print("SAVE: warning: %s is running but not on screen after %d s (%s); saving anyway. Name it with --app %s to refuse such a save." % (
        app, limit, detail, app), flush=True)
    return 0


def wallpaper_wait(q, rundir):
    """on a slow host (6 cores) the wallpaper is drawn 3 to 4 minutes after boot, later than ssh plus the settle, and a
    snapshot saved before it resumes with a black desktop (2 of 9 resumes). Wait, at most WALLWAIT seconds (default
    300, 0 turns it off), until the desktop is drawn (drawn(): more than 500 colours on the sparse sample, or the side columns
    show the wallpaper; a large Safari window used to hold the first test below 500 for the whole WALLWAIT)."""
    limit = float(os.environ.get("WALLWAIT", "300"))
    if limit <= 0:
        return 0.0
    t0 = time.time()
    n = screen_colours(q, rundir + "/wall.ppm")
    if n is not None and not drawn(n):
        print("SAVE: the desktop is not drawn yet (%s colours), waiting for the wallpaper, at most %d s (WALLWAIT)" % (n[0], limit), flush=True)
    last = t0
    while n is not None and not drawn(n) and time.time() - t0 < limit:
        time.sleep(5)
        n = screen_colours(q, rundir + "/wall.ppm")
        if time.time() - last >= 30:
            last = time.time()
            print("SAVE: still waiting for the wallpaper, %d s so far (%s colours)" % (last - t0, n[0] if n else None), flush=True)
    print("SAVE: screen has %s colours (%s at the edges) after %d s of wallpaper wait%s" % (
        n[0] if n else None, n[1] if n else None, time.time() - t0, "" if n is None or drawn(n) else " (limit reached, saving anyway)"), flush=True)
    return time.time() - t0


def sshwait():
    """lib/sshwait.py, loaded by path (this script runs under python3 -I)"""
    import importlib.util
    sp = importlib.util.spec_from_file_location("sshwait", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sshwait.py"))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


def guestapp():
    """lib/guestapp.py (open an app in the guest, is its window on screen), loaded by path like sshwait"""
    import importlib.util
    sp = importlib.util.spec_from_file_location("guestapp", os.path.join(os.path.dirname(os.path.abspath(__file__)), "guestapp.py"))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


def ssh_base(key, port, connect_timeout=8, control=None):
    """ssh to the guest: host key pinned in the per-home known_hosts (accept-new), never with checking switched off"""
    sw = sshwait()
    ctl = ["-o", "ControlMaster=yes", "-o", "ControlPersist=600"] + sw.opt("ControlPath", control) if control else []
    return (["ssh", "-o", "BatchMode=yes"] + sw.hostkey_opts() + ["-o", "LogLevel=ERROR", "-o", "ConnectTimeout=%d" % connect_timeout]
            + ctl + ["-i", key, "-p", str(port), "root@127.0.0.1"])


def master_exit(ssh_cmd, ctl):
    """close the ssh ControlMaster at ctl (no-op when there is none)"""
    subprocess.run(ssh_cmd[:1] + sshwait().opt("ControlPath", ctl) + ["-O", "exit", "root@127.0.0.1"], capture_output=True)


def qemu_alive(rundir):
    """is the QEMU of this run dir still running? It reads rundir/qemu.pid, which the CLI wrote itself a moment ago into a run dir it made
    with mktemp (so the file is never stale). A zombie counts as gone. No pid file: there is nothing to wait for, so True."""
    try:
        pid = int(open(os.path.join(rundir, "qemu.pid")).read())
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    try:
        return open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def save(name, rundir, qemu, cpus, ram, extra, gpu, port, key, settle, pre=""):
    """Build the snapshot in a hidden staging directory; `finish` moves it over NAME only when it is complete, so a failed
    save leaves an older snapshot of that name untouched."""
    d = stage_dir(name, rundir)
    shutil.rmtree(d, ignore_errors=True); os.makedirs(d, mode=0o700)
    try:
        rc = _save(d, name, rundir, qemu, cpus, ram, extra, gpu, port, key, settle, pre)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    if rc != 0:
        shutil.rmtree(d, ignore_errors=True)
    return rc


def _save(d, name, rundir, qemu, cpus, ram, extra, gpu, port, key, settle, pre):
    pick_format(ram)
    json.dump({"fmt": FMT}, open(d + "/fmt.json", "w"))
    ssh = ssh_base(key, port)
    T = {}
    t_all = time.time()
    try:
        t_start = float(open(rundir + "/start").read())
    except Exception:
        t_start = t_all
    q = Qmp(rundir + "/qmp.sock")
    print("SAVE: step 1 of 4: waiting for the guest to boot until ssh answers (a boot takes 1 to 3 minutes; nothing is hung)", flush=True)
    t = time.time()
    # one patient try at a time (lib/sshwait.py), the first at 45 s; a short try abandons a login that is still in progress
    # alive: the wait ends the moment QEMU is gone (it used to retry "connection refused" until SAVE_SSH_WAIT, an hour)
    r, _ = sshwait().wait_ssh(ssh, "true", time.time() + float(os.environ.get("SAVE_SSH_WAIT", "3600")),
                              first_at=float(os.environ.get("SAVE_SSH_FIRST", "45")), t_ref=t_start, alive=lambda: qemu_alive(rundir))
    if r is None:
        print("SAVE failed: ssh never answered" + ("" if qemu_alive(rundir) else " (QEMU is gone)"), file=sys.stderr)
        try: q.cmd("quit")
        except Exception: pass
        return 1
    T["boot_to_ssh_s"] = round(time.time() - t_start, 1)
    print("SAVE: ssh answered %d s after QEMU started" % T["boot_to_ssh_s"], flush=True)
    if pre:
        subprocess.run(ssh + [pre], capture_output=True)
    # one multiplexed connection for the app steps and the clock step (see below); ControlMaster=auto starts it when first used
    ctl = rundir + "/ssh.ctl"
    ssh_c = ssh[:1] + ["-o", "ControlMaster=auto", "-o", "ControlPersist=120"] + sshwait().opt("ControlPath", ctl) + ssh[1:]
    app, required, url = save_app_options()
    if url:
        open_in_guest(ssh_c, app, url)
    print("SAVE: step 2 of 4: settling %s s (the guest is left alone so it goes idle before the save), then waiting for the wallpaper if it is not drawn" % settle, flush=True)
    t = time.time()
    time.sleep(float(settle))
    T["settle_s"] = round(time.time() - t, 1)
    # the clock step used a new ssh login per command; with Safari still loading each login took seconds and the step ran 18 to
    # 57 s. One multiplexed connection, as settime uses, makes every command a round trip on a live session.
    T["wallpaper_wait_s"] = round(wallpaper_wait(q, rundir), 1)
    t = time.time()
    ga = guestapp()
    gate = app_gate(ssh_c, app, required, q, rundir,
                    (lambda: ga.open_app(ssh_c, app, url, verify_s=30, tries=1)) if url else None)
    T["app_wait_s"] = round(time.time() - t, 1)
    if gate != 0:
        master_exit(ssh, ctl)
        try: q.cmd("quit")
        except Exception: pass
        return gate
    t = time.time()
    if os.environ.get("SAVESTEP", "1") == "1":
        print("SAVE: step 3 of 4: setting the guest clock to host time (about 5 to 15 s)", flush=True)
        cs = clock_step(ssh_c)
        print("SAVE: clock step before the save, guest minus host %s s" % (round(cs[1], 2) if cs else "?"), flush=True)
    guest = subprocess.run(ssh_c + ["date +%s"], capture_output=True, text=True).stdout.strip()
    T["clock_step_s"] = round(time.time() - t, 1)
    # close the master before the guest is paused: no ssh session is left open in the saved state
    master_exit(ssh, ctl)
    caps(q)
    q.cmd("migrate-set-parameters", **{"max-bandwidth": 2 ** 40})
    q.cmd("stop")
    wall = time.time()
    state = d + "/vm.state"
    print("SAVE: step 4 of 4: writing the guest RAM and device state (%s GiB, %s format) to %s; the guest is paused" % (
        round(ram_bytes(ram) / 2 ** 30, 1), FMT, state), flush=True)
    q.cmd("migrate", uri=("file:" + state) if FMT == "mapped" else zstd_write_uri(state))
    last = time.time()
    while True:
        m = q.cmd("query-migrate")
        if m.get("status") in ("completed", "failed", "cancelled"): break
        if time.time() - last >= 10:
            last = time.time()
            try:
                done = os.path.getsize(state) / 2 ** 30
            except OSError:
                done = 0.0
            print("SAVE: writing, %d s, %.1f GiB on disk" % (last - wall, done), flush=True)
        time.sleep(0.05)
    if m["status"] != "completed":
        print("SAVE failed: %s" % m.get("error-desc"), file=sys.stderr); q.cmd("quit"); return 1
    while writer_running(state):
        time.sleep(0.1)
    T["migrate_write_s"] = round(time.time() - wall, 1)
    try:
        T["state_gib"] = round(os.path.getsize(state) / 2 ** 30, 2)
        T["write_mb_s"] = round(os.path.getsize(state) / 1e6 / max(T["migrate_write_s"], 0.1))
        T["ram_total_mib"] = round(m.get("ram", {}).get("total", 0) / 2 ** 20)
    except OSError:
        pass
    json.dump({"host_wall": wall, "guest_clock": guest}, open(d + "/saved.json", "w"))
    q.cmd("quit")
    T["save_total_s"] = round(time.time() - t_all, 1)
    json.dump(T, open(d + "/timing.json", "w"))
    print("SAVE: timing, seconds: boot to ssh %(boot_to_ssh_s)s (before this save began counting), settle %(settle_s)s, wallpaper wait %(wallpaper_wait_s)s, "
          "clock step %(clock_step_s)s, state write %(migrate_write_s)s (%(state_gib)s GiB, %(write_mb_s)s MB/s), this save %(save_total_s)s" % T, flush=True)
    return 0


def incoming(name, rundir):
    d = sdir(name)
    m = json.load(open(d + "/meta.json"))
    fmt = m.get("fmt", "zstd")
    q = Qmp(rundir + "/qmp.sock")
    t_load = time.time()
    if fmt == "mapped":
        q.cmd("migrate-set-capabilities", capabilities=[{"capability": "mapped-ram", "state": True},
                                                        {"capability": "multifd", "state": True}])
        q.cmd("migrate-set-parameters", **{"multifd-channels": m.get("channels", 8)})
        q.cmd("migrate-incoming", uri="file:" + d + "/vm.state")
    else:
        q.cmd("migrate-incoming", uri=zstd_read_uri(d + "/vm.state"))
    while True:
        m = q.cmd("query-migrate")
        if m.get("status") == "completed": break
        if m.get("status") in ("failed", "cancelled"):
            print("RESUME failed: %s" % m.get("error-desc"), file=sys.stderr); return 1
        time.sleep(0.02)
    q.cmd("cont")
    json.dump({"t_cont": time.time(), "t_load": t_load}, open(rundir + "/resume.json", "w"))
    return 0


def settime(rundir, port, key, limit="0.5", budget=None):
    """after a resume: wait for ssh, put the guest clock on the host clock, check it, record the timings in
    rundir/resume.json (ssh_s = seconds from cont to the first ssh answer, diff_s = guest minus host after the step).
    The guest clock is read through one running perl (GuestClock), never one spawned process per reading: while the guest
    catches up a spawn takes seconds and its reading is biased by half of that ("off by 1.15 s" with the clock at -0.005 s).
    A reading counts only when its round trip is at most half the limit; slower ones are dropped and sampling goes on, since the
    guest gets quicker as it settles. A guest outside the limit is stepped (settimeofday in that perl, sub-second) and read again,
    until it is within the limit or the budget is used up; every sample is logged in rundir/settime.samples.
    A snapshot older than SYNCMAXAGE is resumed without the counter advance (the overdue work of a long gap makes ssh slow);
    a younger one gets sync-clock-on-load and is already right here.
    The whole thing, ssh wait included, has one budget (BUDGET seconds, default SETTIME_TIMEOUT or 120); the CLI's `timeout`
    around this helper is longer than that, so the helper always ends on its own and closes its ssh master."""
    end = time.time() + float(budget or os.environ.get("SETTIME_TIMEOUT", "120"))
    ctl = rundir + "/ssh.ctl"
    base = ssh_base(key, port, 6, control=ctl)
    try:
        return _settime(rundir, base, limit, end)
    finally:
        master_exit(base, ctl)


STEP_GAP = 15      # seconds between steps once the guest has taken three and is still out (timed puts its own estimate back)


def settle_clock(make, r, limit, end, log, fallback=None):
    """the step-and-check loop of settime; fills r (attempts, method, diff_s, rtt_s). True when a trusted reading is within the limit.
    make() starts a GuestClock (called again when the ssh session under it died, so one dropped connection does not end the check);
    fallback(): a spawned `date` step for a guest whose perl cannot call settimeofday."""
    r["attempts"] = 0
    r["method"] = "none"
    last_step = 0.0
    clock = make()
    try:
        while time.time() < end:
            if clock.p.poll() is not None:
                clock.close(); clock = make()
            b = measure(lambda: clock.sample(max(0.5, min(10.0, end - time.time()))), limit / 2, min(end, time.time() + 30), log=log)
            if b is None:
                continue
            r["diff_s"] = round(b[1], 2); r["rtt_s"] = round(b[0], 3)
            if within(b, limit):
                return True
            if r["attempts"] >= 3 and time.time() - last_step < STEP_GAP:
                time.sleep(1)
                continue
            r["attempts"] += 1
            last_step = time.time()
            if clock.step(last_step + b[0] / 2, max(0.5, min(10.0, end - time.time()))):
                r["method"] = "settimeofday"
            elif fallback and end - time.time() > 25:
                fallback(); r["method"] = "date"
            else:
                time.sleep(1)
        return False
    finally:
        clock.close()


def banner_probe(port, t_cont, out, stop):
    """split "ssh answered N s after cont" into the guest's sshd saying hello (the TCP banner) and the login behind it. A
    probe that connects to the forwarded port and reads the first bytes: slirp forwards to the guest, so SSH- arrives only when the guest's
    sshd is up. Records out["banner_s"] (seconds after cont) once; gives up when stop is set. One short connection per second at most."""
    while not stop.is_set():
        try:
            s = socket.create_connection(("127.0.0.1", int(port)), timeout=30)
            try:
                s.settimeout(30)
                if s.recv(4) == b"SSH-":
                    out["banner_s"] = round(time.time() - t_cont, 1)
                    return
            finally:
                s.close()
        except OSError:
            pass
        stop.wait(1.0)


def banner_note(r):
    """" (sshd banner after B s, login took L s more)" when the probe heard the guest's sshd first, else """""
    b = r.get("ssh_banner_s")
    if b is None or b >= r["ssh_s"]:
        return ""
    return " (the guest's sshd said hello after %.1f s, the login behind it took %.1f s more)" % (b, r["ssh_s"] - b)


def _settime(rundir, base, limit, end):
    import threading
    r = json.load(open(rundir + "/resume.json"))
    probe, stop = {}, threading.Event()
    threading.Thread(target=banner_probe, args=(base[base.index("-p") + 1], r["t_cont"], probe, stop), daemon=True).start()
    # one patient try at a time, never killed before the deadline (a short try abandons a login in progress)
    w, _ = sshwait().wait_ssh(base, "true", end, interval=1, alive=lambda: qemu_alive(rundir))
    if w is None:
        print("SETTIME: ssh never answered"); return 1
    r["ssh_s"] = round(time.time() - r["t_cont"], 2)
    stop.set()
    if "banner_s" in probe:
        r["ssh_banner_s"] = probe["banner_s"]
    try:
        r["ssh_from_start_s"] = round(time.time() - float(open(rundir + "/start").read()), 2)
    except Exception:
        pass
    lim = float(limit)
    trace = open(rundir + "/settime.samples", "w")

    def log(b):
        trace.write("%.1f %s\n" % (time.time() - r["t_cont"], "none" if b is None else "rtt %.3f off %.3f" % b)); trace.flush()

    def date_step():
        g_step(base, 0.4); time.sleep(0.5)

    try:
        ok = settle_clock(lambda: GuestClock(base), r, lim, end, log, fallback=date_step)
    finally:
        trace.close()
    r["time_ok"] = bool(ok)
    json.dump(r, open(rundir + "/resume.json", "w"))
    print("SETTIME: ssh %.1f s after cont%s, method %s, guest minus host %s s (rtt %s), %s" %
          (r["ssh_s"], banner_note(r), r["method"], r.get("diff_s"), r.get("rtt_s"), "OK" if ok else "OUT OF %s s" % limit))
    return 0 if ok else 1


def age(name):
    """seconds since the save: run.sh turns the clock advance on for a snapshot younger than SYNCMAXAGE"""
    try:
        print(int(time.time() - json.load(open(sdir(name) + "/meta.json"))["host_wall"]))
    except Exception:
        print(10 ** 9)
    return 0


def finish(name, rundir, qemu, cpus, ram, extra, gpu):
    """after QEMU and nbd exited: complete the staged snapshot and move it over NAME. The old NAME is set aside first and
    deleted only after the new one is in place, so any failure up to that point leaves the old snapshot as it was."""
    final = sdir(name)
    d = stage_dir(name, rundir)
    try:
        if not os.path.exists(d + "/saved.json"):
            print("SAVE: no state was written", file=sys.stderr)
            shutil.rmtree(d, ignore_errors=True)
            return 1
        for f in ("aux", "root.qcow2"):
            subprocess.run(["cp", "--sparse=always", "%s/%s" % (rundir, f), "%s/%s" % (d, f)], check=True)
        m = meta_for(qemu, cpus, ram, extra, gpu)
        m.update(json.load(open(d + "/saved.json")))
        m["fmt"] = json.load(open(d + "/fmt.json"))["fmt"]; m["channels"] = CHANNELS
        json.dump(m, open(d + "/meta.json.tmp", "w"))
        os.rename(d + "/meta.json.tmp", d + "/meta.json")
        old = os.path.join(SNAPS, ".old-%s-%s" % (name, os.path.basename(os.path.normpath(rundir))))
        had = os.path.isdir(final)
        if had:
            os.rename(final, old)
        try:
            os.rename(d, final)
        except OSError:
            if had:
                os.rename(old, final)
            raise
        if had:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    print("SAVE: %s ready in %s" % (name, final))
    return 0


LEFTOVER_QUIET_S = 120   # an orphan (run dir gone) must have had no write for this long before it is taken for dead


def parse_leftover(entry):
    """`.stage-NAME-RUN` or `.old-NAME-RUN` (RUN = NAME-XXXXXX, the run dir's name) -> (kind, name, run), else None"""
    for kind in ("stage", "old"):
        pre = ".%s-" % kind
        if not entry.startswith(pre):
            continue
        rest = entry[len(pre):]
        n = (len(rest) - 8) // 2
        if n >= 1 and len(rest) == 2 * n + 8 and rest[n] == "-" and rest[n + 1:2 * n + 1] == rest[:n] and rest[2 * n + 1] == "-":
            return kind, rest[:n], rest[n + 1:]
    return None


def newest_write(path):
    """the latest mtime of PATH and what is directly in it"""
    t = os.path.getmtime(path)
    try:
        for e in os.scandir(path):
            t = max(t, e.stat(follow_symlinks=False).st_mtime)
    except OSError:
        pass
    return t


def reap(runs, orphans, *dead):
    """a snapintosh that was SIGKILLed during a save leaves `.stage-*` (the RAM, 4 GiB) and perhaps `.old-*` in SNAPS.
    Those of a run in DEAD (run dir names the CLI proved dead) go; so do orphans, the ones whose run dir is gone and that nothing wrote
    to for LEFTOVER_QUIET_S, but only when ORPHANS is "1" (the CLI passes it when SNAPS is this home's own snaps dir: with a shared SNAPS another home's live save has its run dir elsewhere). `.old-NAME` is the snapshot a save was
    replacing: when NAME is missing (the kill came between the two renames) it is put back, never deleted. Prints what it did."""
    done = 0
    for entry in sorted(os.listdir(SNAPS)) if os.path.isdir(SNAPS) else []:
        p = parse_leftover(entry)
        if not p:
            continue
        kind, name, run = p
        path = os.path.join(SNAPS, entry)
        if not os.path.isdir(path) or not valid_name(name):
            continue
        orphan = orphans == "1" and not os.path.exists(os.path.join(runs, run)) and time.time() - newest_write(path) > LEFTOVER_QUIET_S
        if run not in dead and not orphan:
            continue
        final = os.path.join(SNAPS, name)
        if kind == "old" and not os.path.exists(final):
            os.rename(path, final)
            print("restored snapshot %s from %s (an earlier save was killed between its two renames)" % (name, entry))
        else:
            shutil.rmtree(path, ignore_errors=True)
            print("removed %s left by a snapintosh that was killed (run %s)" % (entry, run))
        done += 1
    return 0


def abort(name, rundir):
    """a save that did not finish: drop its staging directory (the older snapshot of that name is untouched)"""
    shutil.rmtree(stage_dir(name, rundir), ignore_errors=True)
    return 0


def main(a):
    cmds = {"check": lambda: check(*a[1:7]), "save": lambda: save(*a[1:]), "incoming": lambda: incoming(*a[1:3]),
            "age": lambda: age(a[1]), "pick": lambda: pick(*a[1:3]), "keep": lambda: keep(*a[1:3]),
            "settime": lambda: settime(*a[1:6]), "finish": lambda: finish(*a[1:8]), "abort": lambda: abort(*a[1:3]), "reap": lambda: reap(*a[1:])}
    if not a or a[0] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return cmds[a[0]]()
    except KeyboardInterrupt:
        return 130
    except Exception as e:   # any failure is a non-zero exit with a message, never a silent success
        print("snapctl %s failed: %s: %s" % (a[0], type(e).__name__, e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
