#!/usr/bin/env python3
"""snapintosh bench: measure the public benchmark numbers with one command, N runs, JSON plus a text table.

Driven only through the CLI itself (`snapintosh run | snapshot | resume`, run as child processes), ssh and QMP, so it measures
what a user gets. Per run:
  cold run      boot to ssh, boot to Dock (Dock and Finder processes up), Safari cold launch (ssh `open` to the first screendump that
                shows the page, and to the HTTP GET reaching the host), window drag fps (frames counted from the apple_gfx_new_frame trace)
  snapshot      boot, Safari opened during the settle, save (the snapshot has Safari frontmost with page A drawn)
  resume        resume time (migration load to cont), ssh after cont, Safari launch from the resumed snapshot (page B)
Needs no root and no lab files: the HTTP pages and the work dir are made here, the ssh key is the one `make-disk golden` kept.
Env set by `snapintosh bench`: SNAPINTOSH_CLI, BENCH_HOME, BENCH_PREFIX, BENCH_VM, BENCH_SNAPS, BENCH_PCACHE, BENCH_ROOT.
Run under python3 -I. The helper functions above `class Bench` are pure (tests/test_bench.py)."""
import argparse, http.server, json, math, os, re, shutil, signal, socket, statistics, subprocess, sys, threading, time

# (key, label, unit) in table order
METRICS = [
    ("boot_to_ssh_s", "boot to ssh", "s"),
    ("boot_to_dock_s", "boot to Dock", "s"),
    ("safari_cold_draw_s", "Safari cold launch, open to first draw", "s"),
    ("safari_cold_get_s", "Safari cold launch, open to GET at host", "s"),
    ("safari_resume_draw_s", "Safari launch from resumed snapshot", "s"),
    ("safari_resume_get_s", "Safari from resume, open to GET at host", "s"),
    ("drag_fps", "window drag", "fps"),
    ("resume_load_s", "resume time (load to cont)", "s"),
    ("resume_ssh_s", "ssh after resume", "s"),
]
PAGE_RGB = {"a": (0x00, 0xFF, 0xCC), "b": (0xFF, 0x00, 0xCC)}


# ------------------------------------------------------------------ pure helpers
def summarize(values):
    """n, median, min, max, mean, sd (None when fewer than 2) over the numeric values; None entries are dropped"""
    v = [float(x) for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not v:
        return {"n": 0, "median": None, "min": None, "max": None, "mean": None, "sd": None}
    r = lambda x: round(x, 2)
    return {"n": len(v), "median": r(statistics.median(v)), "min": r(min(v)), "max": r(max(v)), "mean": r(statistics.mean(v)),
            "sd": r(statistics.stdev(v)) if len(v) > 1 else None}


def summarize_runs(runs):
    return {k: summarize([r.get(k) for r in runs]) for k, _, _ in METRICS}


def parse_run_dir(text):
    m = re.search(r"run dir: (\S+)", text)
    return m.group(1) if m else None


def parse_renderer(text):
    """the `renderer:` field of the CLI's `run dir:` line: 'lavapipe' or 'GPU /path/to/icd.json'"""
    m = re.search(r"renderer: (GPU \S+|lavapipe)", text)
    return m.group(1) if m else None


def gpu_path(renderer, mesa_found=True):
    """RADV, ANV, NVIDIA, lavapipe or none, from the renderer field (see parse_renderer)"""
    if not renderer:
        return "none"
    if renderer == "lavapipe":
        return "lavapipe" if mesa_found else "none"
    p = renderer.lower()
    for key, name in (("radeon", "RADV"), ("radv", "RADV"), ("intel", "ANV"), ("anv", "ANV"), ("nvidia", "NVIDIA"), ("lvp", "lavapipe")):
        if key in os.path.basename(p):
            return name
    return "GPU (%s)" % os.path.basename(renderer.split(" ", 1)[1])


# The CLI kills its clock-step helper SETTIME_MARGIN s after the budget (snapintosh: timeout -k 5 $((budget + 30))); the bench waits at least that long
SETTIME_MARGIN = 30
SETTIME_LIMIT_MIN = 900


def default_settime_limit(env):
    """seconds to wait for the clock-step line: never shorter than the CLI's own hard limit (budget + margin, plus the 5 s of `timeout -k`)"""
    if env and re.fullmatch(r"[0-9]{1,9}", env):
        return max(SETTIME_LIMIT_MIN, int(env, 10) + SETTIME_MARGIN + 30)
    return SETTIME_LIMIT_MIN


def parse_settime(text):
    """SETTIME line: ssh seconds after cont, guest minus host clock seconds, ok flag. {} when absent."""
    m = re.search(r"SETTIME: ssh ([\d.]+) s after cont.*?guest minus host (-?[\d.]+|None) s.*?, (OK|OUT OF)", text)
    if not m:
        return {}
    return {"resume_ssh_s": float(m.group(1)), "clock_off_s": None if m.group(2) == "None" else float(m.group(2)), "time_ok": m.group(3) == "OK"}


# the end of the clock step in a resume log: the SETTIME result line, or the CLI's note that the step did not confirm
SETTIME_END = r"SETTIME(?::| did not confirm)[^\n]*"


def settime_note(text):
    """why there is no SETTIME result: the first SETTIME line of the log, trimmed"""
    m = re.search(SETTIME_END, text)
    return m.group(0)[:120] if m else "no SETTIME line"


def settime_crashed(text):
    """The clock-step helper died instead of giving up: '' for a clean "did not confirm" (exit status 1, the budget ran out), else the reason.
    The CLI prints "(helper exit status N; ...)": 1 is the helper's own verdict, 124 the CLI's hard `timeout`, 128+n a signal. A Python
    traceback in the log is a crash whatever the status was."""
    if "Traceback (most recent call last)" in text:
        return "the clock-step helper crashed (traceback in the log)"
    m = re.search(r"SETTIME did not confirm[^\n]*?helper exit status (\d+)", text)
    if m and m.group(1) != "1":
        return "the clock-step helper was killed or crashed (exit status %s)" % m.group(1)
    return ""


def parse_ssh_answered(text):
    m = re.search(r"SAVE: ssh answered (\d+) s", text)
    return int(m.group(1)) if m else None


def count_frames(text):
    return sum(1 for line in text.splitlines() if "apple_gfx_new_frame" in line)


def count_fatals(text):
    return len(re.findall(r"fatal|abort|assert|device.?lost|DEVICE_LOST|panic|segfault|SIGSEGV|core dumped", text, re.I))


def cpu_model(cpuinfo):
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "unknown"


def parse_ppm(data):
    """(width, height, pixel bytes) of a binary P6 ppm, or None when it is not complete"""
    try:
        parts = data.split(b"\n", 3)
        if parts[0] != b"P6":
            return None
        w, h = map(int, parts[1].split())
        px = parts[3]
        return (w, h, px) if len(px) >= w * h * 3 else None
    except (ValueError, IndexError):
        return None


def match_points(px, w, h, rgb, step=4, tol=12):
    """x and y lists of the sampled pixels within tol of rgb"""
    xs, ys = [], []
    for y in range(0, h, step):
        for x in range(0, w, step):
            o = (y * w + x) * 3
            if abs(px[o] - rgb[0]) < tol and abs(px[o + 1] - rgb[1]) < tol and abs(px[o + 2] - rgb[2]) < tol:
                xs.append(x); ys.append(y)
    return xs, ys


def page_share(px, w, h, rgb):
    """fraction of a 7 px grid of the screen that has the page colour"""
    xs, _ = match_points(px, w, h, rgb, 7)
    return len(xs) / ((w // 7 + 1) * (h // 7 + 1))


def page_bbox(px, w, h, rgb):
    xs, ys = match_points(px, w, h, rgb)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def fmt(v, unit=""):
    return "-" if v is None else ("%.2f" % v if unit == "fps" else "%.1f" % v)


def format_table(report):
    """box-drawing table: median, min, max, sd, n per metric, with a host header"""
    h = report["host"]
    head = [
        "snapintosh bench  release %s  QEMU md5 %s  %d run%s" % (report["release_commit"], h["qemu_md5"][:8], report["runs_requested"], "" if report["runs_requested"] == 1 else "s"),
        "host: %s, %s threads, guest %s vCPUs / %s, GPU path %s, translation cache %s" % (h["cpu_model"], h["host_threads"], h["cores_given"], h["ram_given"], h["gpu_path"], h["pcache"]),
        "seconds from QEMU start unless noted; spread is min to max and the standard deviation",
    ]
    cols = ["metric", "median", "min", "max", "sd", "n"]
    rows = []
    for k, label, unit in METRICS:
        s = report["summary"][k]
        rows.append(["%s (%s)" % (label, unit), fmt(s["median"], unit), fmt(s["min"], unit), fmt(s["max"], unit), fmt(s["sd"], unit), str(s["n"])])
    wd = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(cols)]

    def line(l, m, r):
        return l + m.join("─" * (w + 2) for w in wd) + r

    def row(cells):
        return "│" + "│".join(" " + (c.ljust(wd[i]) if i == 0 else c.rjust(wd[i])) + " " for i, c in enumerate(cells)) + "│"
    out = head + [line("┌", "┬", "┐"), row(cols), line("├", "┼", "┤")] + [row(r) for r in rows] + [line("└", "┴", "┘")]
    errs = [(r["run"], e) for r in report["runs"] for e in r.get("errors", [])]
    notes = [(r["run"], r["settime_note"]) for r in report["runs"] if r.get("settime_note")]
    if notes:
        out.append("notes (not failures):")
        out += ["  run %s: resume clock step not confirmed: %s" % n for n in notes]
    if errs:
        out.append("problems:")
        out += ["  run %s: %s" % e for e in errs]
    return "\n".join(out) + "\n"


def build_report(host, runs, release_commit, options, stamp):
    return {"schema": 1, "date_utc": stamp, "release_commit": release_commit, "runs_requested": options["runs"], "host": host,
            "options": options, "runs": runs, "summary": summarize_runs(runs)}


# ------------------------------------------------------------------ measurement
def load_guestapp(root):
    import importlib.util
    sp = importlib.util.spec_from_file_location("guestapp", os.path.join(root, "lib", "guestapp.py"))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


def load_sshwait(root):
    import importlib.util
    sp = importlib.util.spec_from_file_location("sshwait", os.path.join(root, "lib", "sshwait.py"))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    return m


class Qmp:
    def __init__(self, path, timeout=30):
        self.s = socket.socket(socket.AF_UNIX); self.s.settimeout(timeout); self.s.connect(path)
        self.f = self.s.makefile("rw"); self.f.readline(); self.cmd("qmp_capabilities")

    def cmd(self, command, **a):
        self.f.write(json.dumps({"execute": command, "arguments": a}) + "\n"); self.f.flush()
        while True:
            d = json.loads(self.f.readline())
            if "return" in d:
                return d["return"]
            if "error" in d:
                raise RuntimeError("%s: %s" % (command, d["error"]))

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class Bench:
    def __init__(self, a):
        e = os.environ
        self.a = a
        self.cli_path = e["SNAPINTOSH_CLI"]; self.root = e["BENCH_ROOT"]
        self.home = e["BENCH_HOME"]; self.prefix = e["BENCH_PREFIX"]; self.vm = e["BENCH_VM"]; self.snaps = e["BENCH_SNAPS"]; self.pcache = e["BENCH_PCACHE"]
        self.key = a.key or os.path.join(self.vm, "golden", "ssh-state", "id_ed25519")
        self.stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        self.work = os.path.join(self.home, "bench", "work-" + self.stamp)
        self.snapname = "bench-" + self.stamp
        self.children = []
        self.gets = []
        self.sshwait = load_sshwait(self.root)
        self.renderer = None
        # one set of VM options for run, snapshot and resume: a snapshot resumes only with the options it was saved with
        self.vmopts = ["--cpus", str(a.cpus), "--ram", a.ram, "--gpu", a.gpu, "--ssh-port", str(a.ssh_port)]
        if a.icd: self.vmopts += ["--icd", a.icd]
        if a.mesa: self.vmopts += ["--mesa", a.mesa]
        if a.pcache == "ro": self.vmopts += ["--pcache-ro"]
        self.ssh_base = (["ssh", "-o", "BatchMode=yes"] + self.sshwait.hostkey_opts()
                         + ["-o", "LogLevel=ERROR", "-o", "ConnectTimeout=30", "-i", self.key, "-p", str(a.ssh_port), "root@127.0.0.1"])

    # -- plumbing
    def http_start(self):
        www = os.path.join(self.work, "www"); os.makedirs(www, exist_ok=True)
        for n, c in (("a", "#00ffcc"), ("b", "#ff00cc")):
            with open("%s/%s.html" % (www, n), "w") as f:
                f.write("<!doctype html><html><head><title>bench%s</title><style>html,body{margin:0;height:100%%;background:%s}</style></head><body></body></html>" % (n, c))
        gets = self.gets

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(s, *x, **k): super().__init__(*x, directory=www, **k)
            def log_message(s, *x): pass
            def do_GET(s): gets.append((time.time(), s.path)); super().do_GET()
            def end_headers(s): s.send_header("Cache-Control", "no-store"); super().end_headers()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.a.http_port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def get_after(self, t, page):
        for ts, p in list(self.gets):
            if ts >= t and p.startswith("/%s.html" % page):
                return round(ts - t, 2)
        return None

    def cli(self, args, log):
        def pre():
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        env = dict(os.environ); env.pop("SNAPINTOSH_DRY", None)
        p = subprocess.Popen([self.cli_path] + args, stdout=open(log, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, preexec_fn=pre, env=env)
        self.children.append(p)
        return p

    @staticmethod
    def read(path):
        try:
            return open(path, errors="replace").read()
        except OSError:
            return ""

    def wait_line(self, log, pat, limit, p=None):
        end = time.time() + limit
        while time.time() < end:
            m = re.search(pat, self.read(log))
            if m:
                return m
            if p is not None and p.poll() is not None:
                return re.search(pat, self.read(log))
            time.sleep(1)
        return None

    def ssh(self, cmd, t=90, ct=30):
        base = [o if not o.startswith("ConnectTimeout=") else "ConnectTimeout=%d" % ct for o in self.ssh_base]
        try:
            r = subprocess.run(base + [cmd], capture_output=True, text=True, timeout=t, stdin=subprocess.DEVNULL)
            return r.returncode
        except subprocess.TimeoutExpired:
            return 124

    def shot(self, rd, tag="s"):
        path = os.path.join(self.work, tag + ".ppm")
        if os.path.exists(path):
            os.remove(path)
        q = Qmp(rd + "/qmp.sock")
        try:
            q.cmd("screendump", filename=path, format="ppm")
        finally:
            q.close()
        end = time.time() + 8
        while time.time() < end:
            try:
                r = parse_ppm(open(path, "rb").read())
            except OSError:
                r = None
            if r:
                return r
            time.sleep(0.2)
        raise RuntimeError("screendump incomplete")

    def wait_page(self, rd, page, limit, iv=1.0):
        """time.time() of the request that first shows the page, or None"""
        rgb = PAGE_RGB[page]; end = time.time() + limit
        while time.time() < end:
            tk = time.time()
            try:
                w, h, px = self.shot(rd)
                if page_share(px, w, h, rgb) > 0.05:
                    return tk
            except Exception:
                pass
            time.sleep(max(0.0, iv - (time.time() - tk)))
        return None

    def open_safari(self, page):
        """through lib/guestapp.py like every other open (detached on the guest, verified by the process, retried when ssh fails);
        returns an object with poll() like the Popen it replaced"""
        ga = load_guestapp(self.root)
        return ga.OpenJob(self.ssh_base, "Safari", "http://10.0.2.2:%d/%s.html" % (self.a.http_port, page), verify_s=60)

    def quit_run(self, rd, p, label):
        """QMP quit, then wait for the CLI (it removes the run dir and starts the compactor); SIGINT and SIGKILL only as bounded fallbacks"""
        try:
            q = Qmp(rd + "/qmp.sock", 15); q.s.settimeout(5)
            try: q.cmd("quit")
            except Exception: pass
            q.close()
        except Exception:
            pass
        try:
            p.wait(120)
        except subprocess.TimeoutExpired:
            self.say("%s: CLI did not exit after quit, sending SIGINT" % label)
            try: os.killpg(p.pid, signal.SIGINT)
            except Exception: pass
            try: p.wait(60)
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid, signal.SIGKILL)
                except Exception: pass

    def say(self, m):
        print("bench: " + m, flush=True)

    # -- drag
    def drag_fps(self, rd, res, errs):
        a = self.a
        time.sleep(a.drag_settle)
        w, h, px = self.shot(rd)
        bb = page_bbox(px, w, h, PAGE_RGB["a"])
        if not bb:
            errs.append("drag: page not on screen before the drag"); return
        x0, y0 = bb[0], bb[1]
        qs = []

        def qopen():
            q = Qmp(rd + "/qmp.sock", 60); qs[:] = [q]; return q

        def qclose():
            for q in qs: q.close()
            qs[:] = []

        def mv(x, y):
            qs[0].cmd("input-send-event", events=[{"type": "abs", "data": {"axis": "x", "value": int(max(0, min(w - 1, x)) * 32767 / (w - 1))}},
                                                  {"type": "abs", "data": {"axis": "y", "value": int(max(0, min(h - 1, y)) * 32767 / (h - 1))}}])

        def btn(down):
            qs[0].cmd("input-send-event", events=[{"type": "btn", "data": {"button": "left", "down": down}}])
        try:
            # find the title bar: a short drag at a few heights above the page, the first that moves the window wins.
            # QMP serves one client at a time, so the connection is closed before every screendump
            gx = gy = None
            for off in (66, 22, 40, 90):
                x, y = x0 + 300, y0 - off
                qopen()
                mv(x, y); time.sleep(0.3); btn(True)
                for i in range(1, 21):
                    mv(x + 150 * i / 20, y + 80 * i / 20); time.sleep(0.05)
                btn(False); qclose(); time.sleep(3)
                w2, h2, px2 = self.shot(rd)
                bb2 = page_bbox(px2, w2, h2, PAGE_RGB["a"])
                if bb2 and abs(bb2[0] - x0) + abs(bb2[1] - y0) > 60:
                    gx, gy = bb2[0] + 300, bb2[1] - off; bb = bb2; break
            if gx is None:
                errs.append("drag: no title bar position moved the Safari window"); return
            res["drag_grab_offset_px"] = off
            qopen()
            qs[0].cmd("trace-event-set-state", name="apple_gfx_new_frame", enable=True)
            mv(gx, gy); time.sleep(0.5); btn(True); t0 = time.time()
            n0 = count_frames(self.read(rd + "/qemu.out"))
            ev = 0
            while time.time() - t0 < a.drag_secs:
                ph = math.sin((time.time() - t0) * 2 * math.pi / 4.0)
                mv(gx + 100 * ph, gy + 60 * ph); ev += 1; time.sleep(0.04)
            t1 = time.time(); n1 = count_frames(self.read(rd + "/qemu.out"))
            btn(False)
            qs[0].cmd("trace-event-set-state", name="apple_gfx_new_frame", enable=False)
            res["drag_events"] = ev
            res["drag_frames"] = n1 - n0; res["drag_s"] = round(t1 - t0, 2)
            res["drag_fps"] = round((n1 - n0) / (t1 - t0), 2)
            if n1 - n0 <= 0:
                errs.append("drag: no apple_gfx_new_frame trace lines in qemu.out"); res["drag_fps"] = None
            qclose(); time.sleep(3)
            try:
                w3, h3, px3 = self.shot(rd)
                bb3 = page_bbox(px3, w3, h3, PAGE_RGB["a"])
                res["drag_window_moved_px"] = abs(bb3[0] - bb[0]) + abs(bb3[1] - bb[1]) if bb3 else None
            except Exception:
                pass
        finally:
            qclose()

    # -- phases
    def cold(self, n, res, errs):
        a = self.a
        log = "%s/cold%d.out" % (self.work, n)
        p = self.cli(["run", "--secs", str(a.secs_cold)] + self.vmopts, log)
        rd = None
        try:
            m = self.wait_line(log, r"run dir: (\S+)", 180, p)
            if not m:
                errs.append("cold: no run dir (%s)" % self.read(log)[-200:].strip().replace("\n", " ")); return
            rd = m.group(1)
            self.renderer = self.renderer or parse_renderer(self.read(log))
            t0 = float(open(rd + "/start").read())
            r, _ = self.sshwait.wait_ssh(self.ssh_base, "true", t0 + a.ssh_limit, first_at=45, t_ref=t0, alive=lambda: p.poll() is None)
            if r is None:
                errs.append("cold: ssh never answered"); return
            res["boot_to_ssh_s"] = round(time.time() - t0, 1)
            end = time.time() + a.dock_limit
            while time.time() < end and p.poll() is None:
                if self.ssh("pgrep -x Dock >/dev/null && pgrep -x Finder >/dev/null", 60) == 0:
                    res["boot_to_dock_s"] = round(time.time() - t0, 1); break
                time.sleep(3)
            if "boot_to_dock_s" not in res:
                errs.append("cold: Dock and Finder never came up"); return
            self.ssh("pmset -a displaysleep 0; true", 60)
            time.sleep(a.settle)
            t_open = time.time(); op = self.open_safari("a")
            d = self.wait_page(rd, "a", a.draw_limit)
            op.poll()
            if d is None:
                errs.append("cold: Safari page not drawn in %d s" % a.draw_limit)
            else:
                res["safari_cold_draw_s"] = round(d - t_open, 1)
            res["safari_cold_get_s"] = self.get_after(t_open, "a")
            if d is not None and not a.no_drag:
                self.drag_fps(rd, res, errs)
            res["cold_fatals"] = count_fatals(self.read(rd + "/qemu.out"))
        except Exception as e:
            errs.append("cold: %r" % (e,))
        finally:
            if rd:
                self.quit_run(rd, p, "cold")
            else:
                self.kill(p)

    def snap_and_resume(self, n, res, errs):
        a = self.a
        slog = "%s/snap%d.out" % (self.work, n)
        p = self.cli(["snapshot", self.snapname, "--secs", str(a.secs_snap), "--settle", str(a.save_settle)] + self.vmopts, slog)
        try:
            if self.wait_line(slog, r"SAVE: ssh answered (\d+) s", a.ssh_limit, p):
                self.ssh("pmset -a displaysleep 0; true", 60)
                self.open_safari("a")
            try:
                p.wait(a.secs_snap)
            except subprocess.TimeoutExpired:
                errs.append("snapshot: save did not finish in %d s" % a.secs_snap); self.kill(p); return
            if p.returncode != 0:
                errs.append("snapshot: CLI rc %s (%s)" % (p.returncode, self.read(slog)[-200:].strip().replace("\n", " "))); return
            try:
                t = json.load(open("%s/%s/timing.json" % (self.snaps, self.snapname)))
                res["snapshot_save_s"] = t.get("save_total_s"); res["snapshot_gib"] = t.get("state_gib")
            except Exception:
                pass
        finally:
            if p.poll() is None:
                self.kill(p)
        rlog = "%s/resume%d.out" % (self.work, n)
        p = self.cli(["resume", self.snapname, "--secs", str(a.secs_resume)] + self.vmopts, rlog)
        rd = None
        try:
            m = self.wait_line(rlog, r"run dir: (\S+)", 180, p)
            if not m:
                errs.append("resume: no run dir (%s)" % self.read(rlog)[-200:].strip().replace("\n", " ")); return
            rd = m.group(1)
            if not self.wait_line(rlog, SETTIME_END, a.settime_limit, p):
                errs.append("resume: no SETTIME line in %d s" % a.settime_limit); return
            res.update(parse_settime(self.read(rlog)))
            # A clock step that did not confirm is a finished step (the guest is running and still loading): record it and go on with
            # the Safari steps instead of dropping the rest of the run (3 of 3 resumes on 6 threads under the old 120 s budget).
            if "time_ok" not in res:
                res["time_ok"] = False
                res["settime_note"] = settime_note(self.read(rlog))
                if "ssh never answered" in res["settime_note"]:
                    errs.append("resume: ssh never answered"); return
                crash = settime_crashed(self.read(rlog))
                if crash:
                    errs.append("resume: " + crash); return
            try:
                j = json.load(open(rd + "/resume.json"))
                res["resume_load_s"] = round(j["t_cont"] - j["t_load"], 2)
            except Exception:
                errs.append("resume: no resume.json")
            time.sleep(a.resume_pause)
            t_open = time.time(); self.open_safari("b")
            d = self.wait_page(rd, "b", a.draw_limit)
            if d is None:
                errs.append("resume: Safari page B not drawn in %d s" % a.draw_limit)
            else:
                res["safari_resume_draw_s"] = round(d - t_open, 1)
            res["safari_resume_get_s"] = self.get_after(t_open, "b")
            res["resume_fatals"] = count_fatals(self.read(rd + "/qemu.out"))
        except Exception as e:
            errs.append("resume: %r" % (e,))
        finally:
            if rd:
                self.quit_run(rd, p, "resume")
            else:
                self.kill(p)
            shutil.rmtree("%s/%s" % (self.snaps, self.snapname), ignore_errors=True)

    def kill(self, p):
        for sig in (signal.SIGINT, signal.SIGKILL):
            try: os.killpg(p.pid, sig)
            except Exception: pass
            try: p.wait(30); return
            except subprocess.TimeoutExpired: pass

    # -- host
    def host_info(self):
        try:
            cpuinfo = open("/proc/cpuinfo").read()
        except OSError:
            cpuinfo = ""
        q = os.path.join(self.prefix, "bin", "qemu-system-aarch64")
        import hashlib
        h = hashlib.md5()
        try:
            with open(q, "rb") as f:
                for blk in iter(lambda: f.read(1 << 20), b""):
                    h.update(blk)
            md5 = h.hexdigest()
        except OSError:
            md5 = "n/a"
        return {"cpu_model": cpu_model(cpuinfo), "host_threads": os.cpu_count(), "cores_given": self.a.cpus, "ram_given": self.a.ram,
                "gpu_path": gpu_path(self.renderer), "qemu_md5": md5, "pcache": self.a.pcache, "ipsw_build": os.environ.get("BENCH_IPSW_BUILD", "")}

    def release_commit(self):
        if self.a.commit:
            return self.a.commit
        try:
            r = subprocess.run(["git", "-C", self.root, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def preflight(self):
        miss = []
        if not os.access(os.path.join(self.prefix, "bin", "qemu-system-aarch64"), os.X_OK): miss.append("QEMU not built (snapintosh build)")
        if not os.path.isfile(self.key): miss.append("no ssh key %s (make-disk golden)" % self.key)
        if not (os.path.isfile(os.path.join(self.vm, "golden", "root-quiet.qcow2")) or os.path.isfile(os.path.join(self.vm, "golden", "root-sshd.qcow2"))):
            miss.append("no golden image (make-disk golden)")
        if not shutil.which("ssh"): miss.append("ssh missing")
        return miss

    def pack_present(self):
        try:
            return any(f.endswith(".pack") for f in os.listdir(self.pcache))
        except OSError:
            return False

    def run(self):
        a = self.a
        os.makedirs(self.work, exist_ok=True)
        self.http_start()
        runs = []
        try:
            for n in range(1, a.runs + 1):
                res = {"run": n}; errs = []
                self.say("run %d of %d: cold boot, Safari launch, drag" % (n, a.runs))
                self.cold(n, res, errs)
                if not a.no_resume:
                    self.say("run %d of %d: snapshot and resume" % (n, a.runs))
                    self.snap_and_resume(n, res, errs)
                res["errors"] = errs
                runs.append(res)
                self.say("run %d: %s" % (n, json.dumps({k: v for k, v in res.items() if k != "errors"})))
                if errs:
                    self.say("run %d problems: %s" % (n, "; ".join(errs)))
        finally:
            for p in self.children:
                if p.poll() is None:
                    self.kill(p)
            shutil.rmtree("%s/%s" % (self.snaps, self.snapname), ignore_errors=True)
            try: self.srv.shutdown()
            except Exception: pass
        report = build_report(self.host_info(), runs, self.release_commit(), {k: v for k, v in vars(a).items() if k not in ("key", "icd", "mesa", "out")}, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return report


def parse_args(argv):
    p = argparse.ArgumentParser(prog="snapintosh bench", description="Measure boot, Dock, Safari launch, drag fps and resume time.")
    p.add_argument("--runs", type=int, default=3, help="runs (default 3)")
    p.add_argument("--out", default=None, help="directory for the JSON and text results (default $SNAPINTOSH_HOME/bench)")
    p.add_argument("--cpus", type=int, default=12, help="guest vCPUs (default 12)")
    p.add_argument("--ram", default="8G")
    p.add_argument("--gpu", default="auto", help="auto|0|1")
    p.add_argument("--icd"); p.add_argument("--mesa")
    p.add_argument("--ssh-port", type=int, default=10022)
    p.add_argument("--http-port", type=int, default=18080, help="host port of the page server the guest opens (guest sees it as 10.0.2.2)")
    p.add_argument("--key", help="guest ssh key (default: the golden's)")
    p.add_argument("--pcache", choices=("ro", "rw"), default="ro", help="translation cache mode (default ro: measure the cache as shipped)")
    p.add_argument("--settle", type=int, default=60, help="seconds between Dock and the Safari cold launch (default 60)")
    p.add_argument("--save-settle", type=int, default=120, help="seconds between the first ssh answer and the snapshot save; Safari opens inside it (default 120)")
    p.add_argument("--settime-limit", type=int, default=None, help="seconds to wait for the clock step line after a resume (default: the CLI's hard limit, SETTIME_TIMEOUT + %d s, and at least %d)" % (SETTIME_MARGIN, SETTIME_LIMIT_MIN))
    p.add_argument("--drag-settle", type=int, default=40); p.add_argument("--drag-secs", type=int, default=20)
    p.add_argument("--resume-pause", type=int, default=3)
    p.add_argument("--no-drag", action="store_true"); p.add_argument("--no-resume", action="store_true", help="skip the snapshot and resume part (about 10 minutes per run)")
    p.add_argument("--commit", help="release commit to record (default: git HEAD of the CLI directory)")
    p.add_argument("--keep", action="store_true", help="keep the work dir with the CLI logs")
    p.add_argument("--dry-run", action="store_true", help="print the plan and the host info, run nothing")
    p.add_argument("--ssh-limit", type=int, default=900); p.add_argument("--dock-limit", type=int, default=600); p.add_argument("--draw-limit", type=int, default=300)
    p.add_argument("--secs-cold", type=int, default=1800); p.add_argument("--secs-snap", type=int, default=1800); p.add_argument("--secs-resume", type=int, default=1200)
    a = p.parse_args(argv)
    if a.runs < 1:
        p.error("--runs must be at least 1")
    if a.settime_limit is None:
        a.settime_limit = default_settime_limit(os.environ.get("SETTIME_TIMEOUT"))
    return a


def main(argv):
    a = parse_args(argv)
    if os.environ.get("SNAPINTOSH_DRY") == "1":
        a.dry_run = True
    b = Bench(a)
    miss = b.preflight()
    if a.dry_run:
        print("snapintosh bench (dry run): %d run(s), guest %d vCPUs %s, gpu %s, cache %s" % (a.runs, a.cpus, a.ram, a.gpu, a.pcache))
        print("per run: cold boot (ssh, Dock, Safari cold launch%s)%s" % ("" if a.no_drag else ", drag %ds" % a.drag_secs, "" if a.no_resume else ", snapshot, resume, Safari from resume"))
        print("host: %s" % json.dumps(b.host_info()))
        for m in miss: print("would fail: " + m)
        if not b.pack_present(): print("note: no translation cache pack yet, the first boot will be slow (snapintosh make-disk warm 2)")
        return 0
    if miss:
        for m in miss: print("snapintosh: " + m, file=sys.stderr)
        return 1
    if not b.pack_present():
        print("bench: warning: no translation cache pack in %s; run `snapintosh make-disk warm 2` first or the first boots measure an empty cache" % b.pcache, flush=True)
    if socket.socket().connect_ex(("127.0.0.1", a.ssh_port)) == 0:
        print("snapintosh: something already listens on port %d (another VM running?)" % a.ssh_port, file=sys.stderr); return 1
    try:
        report = b.run()
    finally:
        if not a.keep:
            shutil.rmtree(b.work, ignore_errors=True)
    out = a.out or os.path.join(b.home, "bench"); os.makedirs(out, exist_ok=True)
    jp = os.path.join(out, "bench-%s.json" % b.stamp)
    with open(jp, "w") as f:
        json.dump(report, f, indent=1); f.write("\n")
    table = format_table(report)
    with open(jp[:-5] + ".txt", "w") as f:
        f.write(table)
    print(table)
    print("results: %s (and .txt)" % jp)
    return 0 if all(not r.get("errors") for r in report["runs"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
