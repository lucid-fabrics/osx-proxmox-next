"""check-host limits and fixes, a release tree under the build's mount point, the resume clock-step budget, the bench
treating an unconfirmed clock step as a finished step, and every subcommand's usage line listing the options it accepts.
Run: python3 -I tests/test_hostgaps.py"""
import importlib.util, os, re, shutil, stat, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(HERE, "snapintosh")
T = tempfile.mkdtemp(prefix="hg-")
fails = []


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else "  " + str(detail)[:600]))
    if not cond:
        fails.append(name)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)
    os.chmod(path, mode)


def run(args, env=None, cwd=None, timeout=120):
    e = dict(os.environ, SNAPINTOSH_HOME=T + "/home", XDG_CONFIG_HOME=T + "/cfg", HOME=T + "/hm")
    e.pop("SNAPINTOSH_NO_REPRO", None)
    e.update(env or {})
    r = subprocess.run([CLI] + args, env=e, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return r.returncode, r.stdout + r.stderr


def host(threads=None, mem_kb=None, **env):
    e = dict(env)
    if threads is not None: e["SNAPINTOSH_CHECK_THREADS"] = str(threads)
    if mem_kb is not None: e["SNAPINTOSH_CHECK_MEM_KB"] = str(mem_kb)
    return run(["check-host"], e)[1]


GIB = 1048576  # kB per GiB
# ---- 1. check-host: threads and RAM limits match the README's smallest tested host (6 threads, 16 GiB)
out = host(threads=6, mem_kb=int(15.3 * GIB))
check("6-threads-is-not-a-fail", "[FAIL]" not in "\n".join(l for l in out.splitlines() if "threads" in l) and "6 threads" in out, out)
check("6-threads-warns", re.search(r"\[warn\].*6 threads", out) is not None, out)
check("5-threads-fails", re.search(r"\[FAIL\].*5 threads", host(threads=5)) is not None)
check("8-threads-warns", re.search(r"\[warn\].*8 threads", host(threads=8)) is not None)
check("16-threads-ok", re.search(r"\[ok\].*16 threads", host(threads=16)) is not None)
check("16GiB-host-showing-15-passes", re.search(r"\[warn\].*RAM 15 GiB", out) is not None and not re.search(r"\[FAIL\].*RAM", out), out)
# an iGPU carve-out or a crashkernel shows 13 to 14 GiB on a 16 GiB machine
check("14GiB-with-unreadable-dmi-warns", re.search(r"\[warn\].*RAM 14 GiB", host(mem_kb=int(14.9 * GIB), SNAPINTOSH_CHECK_DMI_GB="")) is not None)
out = host(mem_kb=int(13.2 * GIB), SNAPINTOSH_CHECK_DMI_GB="16")
check("13GiB-of-16-installed-warns", re.search(r"\[warn\].*RAM 13 GiB visible of 16 GiB installed", out) is not None and not re.search(r"\[FAIL\].*RAM", out), out)
check("14GiB-of-8-installed-fails", re.search(r"\[FAIL\].*RAM 14 GiB \(8 GiB installed\)", host(mem_kb=int(14.9 * GIB), SNAPINTOSH_CHECK_DMI_GB="8")) is not None)
check("12GiB-fails", re.search(r"\[FAIL\].*RAM 12 GiB", host(mem_kb=int(12.9 * GIB), SNAPINTOSH_CHECK_DMI_GB="16")) is not None)
check("24GiB-host-showing-23-ok", re.search(r"\[ok\].*RAM 23 GiB", host(mem_kb=int(23.4 * GIB))) is not None)

# ---- 2. the IPSW counts only while there is no imported disk
home = T + "/home"
out = host()
check("no-disk-counts-the-ipsw", "25 IPSW" in out, out)
write(home + "/vm/disk.img", "x")
out = host()
check("imported-disk-does-not-count-the-ipsw", "IPSW" not in out and "31 disk" not in out, out)
os.remove(home + "/vm/disk.img")

# ---- 3. rustup tools in ~/.cargo/bin are found when PATH lacks it (non-interactive ssh)
for t in ("cargo", "rustc", "rustup", "make", "cpio"):
    write(T + "/hm/.cargo/bin/" + t, "#!/bin/sh\nexit 0\n", 0o755)
bare = {"PATH": "/usr/bin:/bin"}
for t in ("cargo", "rustc", "rustup"):
    if shutil.which(t, path="/usr/bin:/bin"): bare = None
if bare:
    out = host(**bare)
    check("cargo-bin-found-without-path", "Ambrosia build tools: cargo rustc rustup make cpio" in out and "missing Ambrosia" not in out, out)
    out = host(PATH="/usr/bin:/bin", CARGO_HOME=T + "/nowhere")
    check("no-cargo-bin-still-reports-missing", "missing Ambrosia build tools" in out or shutil.which("cargo", path="/usr/bin:/bin"), out)

# ---- 4. udmabuf: the parameters, the access and the exact fix
write(T + "/ub/params/list_limit", "1024\n"); write(T + "/ub/params/size_limit_mb", "64\n")
write(T + "/ub/dev", "", 0o000)
env = {"SNAPINTOSH_UDMABUF_DEV": T + "/ub/dev", "SNAPINTOSH_UDMABUF_PARAMS": T + "/ub/params"}
out = host(**env)
check("udmabuf-reports-parameters", "udmabuf list_limit=1024 size_limit_mb=64" in out, out)
check("udmabuf-prints-the-limit-fix", "echo 65536 | sudo tee " + T + "/ub/params/list_limit" in out and "echo 4096 | sudo tee" in out and "/etc/modprobe.d/snapintosh-udmabuf.conf" in out, out)
if os.geteuid() != 0:
    check("udmabuf-prints-the-access-fix", "sudo usermod -aG " in out and "udev/rules.d" in out and "not accessible" in out, out)
os.chmod(T + "/ub/dev", 0o660)
write(T + "/ub/params/list_limit", "65536\n"); write(T + "/ub/params/size_limit_mb", "4096\n")
out = host(**env)
check("udmabuf-good-setup-is-quiet", "sudo tee " + T + "/ub" not in out and "snapintosh-udmabuf" not in out and "usermod" not in out and "readable and writable" in out, out)
out = host(SNAPINTOSH_UDMABUF_DEV=T + "/ub/missing", SNAPINTOSH_UDMABUF_PARAMS=T + "/ub/params")
check("udmabuf-missing-prints-modprobe", "sudo modprobe udmabuf" in out and "modules-load.d" in out, out)

# ---- 5. a release tree (or anything else the build reads) under the build's mount point
MNT = T + "/m"
os.makedirs(MNT)
ns_ok = subprocess.run(["unshare", "-rm", "true"], capture_output=True).returncode == 0
if not ns_ok:
    print("skip  mount-namespace tests (unprivileged user namespaces are off here)")
else:
    shutil.copytree(HERE, MNT + "/rel", ignore=shutil.ignore_patterns(".git", "__pycache__", "docs", "mac", "tests"))
    # a cargo that does what the real one needs from the toolchain file: read it, then leave the dist tree
    write(T + "/bin/cargo", '#!/bin/sh\n[ -r "$CMAKE_TOOLCHAIN_FILE" ] || { echo "Could not find toolchain file $CMAKE_TOOLCHAIN_FILE" >&2; exit 7; }\n'
          'mkdir -p "$CARGO_TARGET_DIR/dist-vulkan/lib/pkgconfig"; : > "$CARGO_TARGET_DIR/dist-vulkan/lib/pkgconfig/reims-vgpu.pc"\n', 0o755)
    for t in ("rustc", "clang", "ld.lld", "lld-link", "make", "cmake", "rustup"):
        write(T + "/bin/" + t, "#!/bin/sh\nexit 0\n", 0o755)
    g = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    refs = {}
    for n in ("reims", "m2v"):
        d = T + "/repo-" + n
        os.makedirs(d)
        subprocess.run(["git", "-C", d, "init", "-q"], check=True, env=g)
        write(d + "/f", n)
        subprocess.run(["git", "-C", d, "add", "f"], check=True, env=g)
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", "x"], check=True, env=g)
        refs[n] = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    benv = {"PATH": T + "/bin:" + os.environ["PATH"], "SNAPINTOSH_REPRO_MNT": MNT, "REIMS_REPO": "file://" + T + "/repo-reims", "REIMS_REF": refs["reims"],
            "M2V_REPO": "file://" + T + "/repo-m2v", "M2V_REF": refs["m2v"], "SNAPINTOSH_HOME": T + "/bh"}
    r = subprocess.run([MNT + "/rel/snapintosh", "build", "--only", "reims"], env=dict(os.environ, HOME=T + "/hm", XDG_CONFIG_HOME=T + "/cfg", **benv),
                       capture_output=True, text=True, timeout=120)
    check("release-tree-under-the-mount-point-builds", r.returncode == 0 and "reims-vgpu dist" in r.stdout, r.stdout + r.stderr)
    # a home under the mount point cannot work: refused before anything is cloned or built
    os.makedirs(MNT + "/home")
    shutil.rmtree(T + "/bh", ignore_errors=True)
    r = subprocess.run([CLI, "build", "--only", "reims"], env=dict(os.environ, XDG_CONFIG_HOME=T + "/cfg", **dict(benv, HOME=MNT + "/home")),
                       capture_output=True, text=True, timeout=120)
    check("home-under-the-mount-point-is-refused-early", r.returncode != 0 and "hides these paths" in r.stderr and "HOME=" + MNT + "/home" in r.stderr
          and "SNAPINTOSH_NO_REPRO=1" in r.stderr and not os.path.exists(T + "/bh/src"), r.stdout + r.stderr)
    # the escape hatch builds in place
    r = subprocess.run([CLI, "build", "--only", "reims"], env=dict(os.environ, XDG_CONFIG_HOME=T + "/cfg", **dict(benv, HOME=MNT + "/home", SNAPINTOSH_NO_REPRO="1")),
                       capture_output=True, text=True, timeout=120)
    check("no-repro-escape-hatch-builds-in-place", r.returncode == 0, r.stdout + r.stderr)
    out = subprocess.run([CLI, "check-host"], env=dict(os.environ, XDG_CONFIG_HOME=T + "/cfg", **dict(benv, HOME=MNT + "/home")), capture_output=True, text=True).stdout
    check("check-host-warns-about-a-home-under-the-mount-point", re.search(r"\[warn\].*build will refuse.*HOME=", out) is not None, out)

# the detection itself needs no namespace: HOME, CARGO_HOME, RUSTUP_HOME and the build tools under the mount point are found, symlinks included
src = open(CLI).read()
hits_fn = re.search(r"^repro_mnt_hits\(\) \{.*?^\}", src, re.M | re.S).group(0)
def hits(**env):
    r = subprocess.run(["bash", "-c", "REPRO_MNT=%s\n%s\nrepro_mnt_hits" % (MNT, hits_fn)], env=dict(dict(os.environ, HOME=T + "/hm", TMPDIR="/tmp", CARGO_HOME="", RUSTUP_HOME=""), **env),
                       capture_output=True, text=True)
    return r.stdout.split()
os.makedirs(MNT + "/home", exist_ok=True); os.makedirs(MNT + "/tools", exist_ok=True)
write(MNT + "/tools/cargo", "#!/bin/sh\n", 0o755)
check("mnt-hits-none-for-a-clean-setup", hits() == [], hits())
check("mnt-hits-home-and-the-cargo-dirs-below-it", hits(HOME=MNT + "/home") == ["HOME=" + MNT + "/home", "CARGO_HOME=" + MNT + "/home/.cargo", "RUSTUP_HOME=" + MNT + "/home/.rustup"], hits(HOME=MNT + "/home"))
check("mnt-hits-cargo-home-and-rustup-home", hits(CARGO_HOME=MNT + "/c", RUSTUP_HOME=MNT + "/r") == ["CARGO_HOME=" + MNT + "/c", "RUSTUP_HOME=" + MNT + "/r"])
check("mnt-hits-a-tool-on-path", hits(PATH=MNT + "/tools:" + os.environ["PATH"]) == ["cargo=" + MNT + "/tools/cargo"])
os.makedirs(T + "/real", exist_ok=True)
os.symlink(MNT + "/tools/cargo", T + "/real/cargo")
check("mnt-hits-a-tool-reached-through-a-symlink", hits(PATH=T + "/real:" + os.environ["PATH"]) == ["cargo=" + T + "/real/cargo"], hits(PATH=T + "/real:" + os.environ["PATH"]))
check("mnt-hits-a-sibling-path-is-not-a-hit", hits(HOME=MNT + "x/home") == [])

# ---- 6. the resume clock-step budget scales with the host, SETTIME_TIMEOUT overrides it
src = open(CLI).read()
fn = re.search(r"^dec_into\(\) \{.*?^\}", src, re.M | re.S).group(0) + "\n" + re.search(r"^settime_budget\(\) \{.*?^\}", src, re.M | re.S).group(0)
def budget(threads, **env):
    r = subprocess.run(["bash", "-c", 'die() { echo "$*" >&2; exit 1; }\n' + fn + "\nsettime_budget"], env=dict(dict(os.environ, SNAPINTOSH_CHECK_THREADS=str(threads), SETTIME_TIMEOUT=""), **env),
                       capture_output=True, text=True)
    return r.returncode, r.stdout.strip()
check("settime-budget-16-threads-120", budget(16) == (0, "120"), budget(16))
check("settime-budget-12-threads-120", budget(12) == (0, "120"))
check("settime-budget-8-threads-300", budget(8) == (0, "300"))
check("settime-budget-6-threads-600", budget(6) == (0, "600"), budget(6))
check("settime-timeout-overrides", budget(6, SETTIME_TIMEOUT="900") == (0, "900") and budget(16, SETTIME_TIMEOUT="40") == (0, "40"))
check("settime-timeout-must-be-a-number", budget(6, SETTIME_TIMEOUT="10m")[0] == 1)
check("settime-timeout-leading-zero-is-decimal", budget(6, SETTIME_TIMEOUT="0900") == (0, "900") and budget(6, SETTIME_TIMEOUT="08") == (0, "8") and budget(6, SETTIME_TIMEOUT="0600") == (0, "600"), [budget(6, SETTIME_TIMEOUT=v) for v in ("0900", "08", "0600")])
check("settime-timeout-rejects-a-sign-and-huge-numbers", budget(6, SETTIME_TIMEOUT="-5")[0] == 1 and budget(6, SETTIME_TIMEOUT="99999999999999999999")[0] == 1)
check("check-threads-with-a-leading-zero-is-decimal", budget("08") == (0, "300"), budget("08"))
check("resume-uses-the-budget-for-both-the-limit-and-the-helper", 'timeout -k 5 $((stb + 30)) python3 -I "$LIB/snapctl.py" settime "$R" "$SSHPORT" "$key" 0.5 "$stb"' in src)
check("settime-timeout-documented-in-usage", "SETTIME_TIMEOUT=SECONDS" in src.split("EOF")[0] or "SETTIME_TIMEOUT=SECONDS" in run(["--help"])[1])

# ---- 7. bench: an unconfirmed clock step is a finished step
sp = importlib.util.spec_from_file_location("benchctl", os.path.join(HERE, "lib", "benchctl.py"))
b = importlib.util.module_from_spec(sp); sp.loader.exec_module(b)
log = "run dir: /x/r\nSETTIME did not confirm the guest clock (the guest is running; budget 630 s, raise it with SETTIME_TIMEOUT=SECONDS)\n"
check("bench-recognises-the-unconfirmed-line", re.search(b.SETTIME_END, log) is not None)
check("bench-recognises-the-result-line", re.search(b.SETTIME_END, "SETTIME: ssh 1.6 s after cont, method none, guest minus host 0.1 s (rtt 0.2), OK\n") is not None)
check("bench-unconfirmed-has-no-result", b.parse_settime(log) == {})
check("bench-note-names-the-reason", b.settime_note(log).startswith("SETTIME did not confirm"), b.settime_note(log))
check("bench-save-settle-flag-default-120", b.parse_args([]).save_settle == 120 and b.parse_args(["--save-settle", "300"]).save_settle == 300)
# a clock-step helper that crashed or was killed is a failure; only a clean "did not confirm" (exit status 1) is a note
def sline(status): return "SETTIME did not confirm the guest clock (helper exit status %s; the guest is running; budget 630 s)\n" % status
check("bench-clean-give-up-is-no-crash", b.settime_crashed(sline(1)) == "", b.settime_crashed(sline(1)))
check("bench-timeout-kill-is-a-crash", "124" in b.settime_crashed(sline(124)), b.settime_crashed(sline(124)))
check("bench-signal-exit-is-a-crash", b.settime_crashed(sline(137)) != "" and b.settime_crashed(sline(2)) != "")
check("bench-traceback-is-a-crash", b.settime_crashed("Traceback (most recent call last):\n  File x\n" + sline(1)) != "")
bsrc = open(os.path.join(HERE, "lib", "benchctl.py")).read()
check("bench-resume-fails-the-run-on-a-crashed-helper", "settime_crashed(self.read(rlog))" in bsrc and 'errs.append("resume: " + crash)' in bsrc)
check("cli-settime-line-has-the-exit-status", "helper exit status $?" in src)
# --settime-limit follows SETTIME_TIMEOUT plus the CLI's margin (30 s, and 5 s of timeout -k), never shorter than the CLI's hard limit
def lim(env=None):
    e = os.environ.pop("SETTIME_TIMEOUT", None)
    if env is not None: os.environ["SETTIME_TIMEOUT"] = env
    try: return b.parse_args([]).settime_limit
    finally:
        os.environ.pop("SETTIME_TIMEOUT", None)
        if e is not None: os.environ["SETTIME_TIMEOUT"] = e
check("settime-limit-default-is-900", lim() == 900, lim())
check("settime-limit-follows-a-raised-timeout", lim("900") >= 900 + 30 + 5 and lim("2000") >= 2000 + 30 + 5, [lim("900"), lim("2000")])
check("settime-limit-leading-zero-is-decimal", lim("0900") == lim("900"))
check("settime-limit-explicit-wins", b.parse_args(["--settime-limit", "100"]).settime_limit == 100)
check("bench-settime-limit-covers-the-slowest-budget", b.parse_args([]).settime_limit >= 600 + 30)
rep = b.build_report({"cpu_model": "x", "host_threads": 6, "cores_given": 6, "ram_given": "4G", "gpu_path": "lavapipe", "qemu_md5": "ab", "pcache": "ro"},
                     [{"run": 1, "boot_to_ssh_s": 1.0, "time_ok": False, "settime_note": "SETTIME did not confirm", "errors": []}], "abc", {"runs": 1}, "2026-11-20T00:00:00Z")
tab = b.format_table(rep) if hasattr(b, "format_table") else b.render_table(rep)
check("bench-table-shows-the-note-not-a-failure", "not confirmed" in tab and "problems:" not in tab, tab)

# ---- 8. usage: every option a subcommand accepts is in its usage text
usage = run(["--help"])[1]
def fn_body(name):
    m = re.search(r"^%s\(\) \{.*?^\}" % name, src, re.M | re.S)
    return m.group(0)
def opts(body):
    # case labels only: `--x)`, `--x|--y)` (a label ends in `)` before the action)
    out = set()
    for line in body.splitlines():
        m = re.match(r"\s*((?:-{1,2}[A-Za-z][\w-]*\|?)+)\)", line)
        if m: out |= set(x for x in m.group(1).split("|") if x)
        for m in re.finditer(r"(?:^|;|\s)(--[a-z][\w-]*)\)", line): out.add(m.group(1))
    return out
def block(start, end):
    i = usage.index(start); j = usage.index(end, i + len(start)); return usage[i:j]
SKIP = {"-h", "--help", "--password"}   # help is universal, --password only dies with a pointer to --password-file
checks = {
    "build": (opts(fn_body("cmd_build")), block("  build [--only", "  fetch ")),
    "fetch": (opts(fn_body("cmd_fetch")), block("  fetch [--build", "  make-disk <step>")),
    "make-disk golden": (opts(fn_body("make_disk_golden")), block("      golden [BASE]", "      warm [N]")),
    "make-disk import": (opts(fn_body("make_disk_import")) | {"--avpbooter"}, block("      import DIR", "      adopt-golden")),
    "make-disk adopt-golden": (opts(fn_body("make_disk_adopt_golden")), block("      adopt-golden", "      golden [BASE]")),
    "run/snapshot/resume": (opts(fn_body("parse_run_opts")), usage[usage.index("run/snapshot/resume options:"):]),
    "make-disk warm": (opts(fn_body("parse_run_opts")), block("      warm [N]", "      sshd-interactive") + usage[usage.index("run/snapshot/resume options:"):]),
}
for name, (acc, text) in checks.items():
    acc = {o for o in acc if o not in SKIP}
    missing = sorted(o for o in acc if o not in text)
    check("usage-lists-every-option-of-" + name.replace(" ", "-").replace("/", "-"), acc and not missing, "missing in the usage text: %s (accepted: %s)" % (missing, sorted(acc)))
# the golden run options in the golden line are the ones it passes on to phase 2
g = block("      golden [BASE]", "      warm [N]")
for o in ("--ram", "--icd", "--cpus", "--gpu", "--mesa", "--ssh-port", "--secs"):
    check("golden-usage-line-has-" + o, o in g)
# bench: the main usage text may only name flags that benchctl's parser really has (`bench --help` is argparse's own output and always lists every
# flag, so checking it against the parser proves nothing), and `bench --help` has to run
bench_flags = set(re.findall(r'add_argument\("(--[a-z-]+)"', open(os.path.join(HERE, "lib", "benchctl.py")).read()))
bench_block = block("  bench [options]", "  status ")
named = set(re.findall(r"--[a-z][a-z-]*", bench_block)) - {"--help"}
check("main-usage-bench-names-only-real-flags", bool(named) and named <= bench_flags, sorted(named - bench_flags))
check("bench-help-runs", subprocess.run([CLI, "bench", "--help"], capture_output=True, text=True, env=dict(os.environ, SNAPINTOSH_HOME=T + "/home", HOME=T + "/hm")).returncode == 0)
check("main-usage-mentions-bench-save-settle", "--save-settle" in usage)

shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if fails else 0)
