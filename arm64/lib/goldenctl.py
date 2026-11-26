#!/usr/bin/env python3
"""goldenctl.py: helpers for `snapintosh make-disk golden`.

  goldenctl.py extra SOCK HELPERDIR GUESTDIR [--user U]
        the autologin password is read from the environment (SNAPINTOSH_GUEST_PASSWORD), never from the command line.
        single-user shell on the serial socket SOCK: stage and run guest/golden-phase1.sh (sshd Interactive, timed fix,
        autologin fallback), then sync and halt. HELPERDIR holds persist-sshd.py and serial-command.py (from the QEMU fork).
  goldenctl.py warm RUNDIR PORT KEY [TIMEOUT]
        normal boot of the finished golden: wait for ssh and the desktop, settle, open Safari on a local page, wait until its
        WebContent process runs, quit Safari, shut the guest down. Only purpose: the QEMU translation cache pack (rw) learns the boot.
  goldenctl.py quiet RUNDIR PORT KEY QUIETSH [TIMEOUT]
        normal boot: wait for ssh (proves the phase 1 sshd), run apply-quiet.sh, check the result, shut the guest down.
        Writes RUNDIR/golden.json with the timings and the result."""
import importlib.util, json, os, re, shlex, socket, subprocess, sys, time


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


def valid_user(u):
    """a macOS short name as we accept it: lower case letters, digits, underscore, dash, 32 at most, not starting with a digit or dash"""
    return isinstance(u, str) and USER_RE.fullmatch(u) is not None


def kcpassword(pw):
    key = bytes([0x7D, 0x89, 0x52, 0x23, 0xD2, 0xBC, 0xDD, 0xEA, 0xA3, 0xB9, 0x1F])
    b = pw.encode()
    b += b"\0" * ((12 - len(b) % 12) % 12 or (12 if len(b) % 12 == 0 else 0))
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(b))


def extra(sock, helper, guestdir, user, password):
    if not valid_user(user):
        raise ValueError("invalid user name %r (lower case letters, digits, _ and -, at most 32)" % (user,))
    ps = load(os.path.join(helper, "persist-sshd.py"), "persist_sshd")
    from pathlib import Path
    sp = Path(sock)
    stage = ps.discover_stage(sp)
    ps.upload(sp, stage, "golden-phase1.sh", open(os.path.join(guestdir, "golden-phase1.sh"), "rb").read(), 0o700)
    ps.upload(sp, stage, "kcpassword", kcpassword(password), 0o600)
    out = ps.run_serial(sp, "AUTOLOGIN_USER=%s %s" % (shlex.quote(user), shlex.quote(stage + "/golden-phase1.sh")), timeout=300)
    print(out, end="")
    if "golden phase 1 done" not in out:
        raise RuntimeError("golden-phase1.sh did not finish")
    try:
        ps.run_serial(sp, "sync; sync; /sbin/halt -q", timeout=30)
    except Exception as e:   # the shell dies with the machine: expected
        print("halt sent (%s)" % type(e).__name__)
    return 0


def sh(args, timeout):
    # a booting guest answers ssh slowly: a timeout is a failed try (rc 255), not a crash that leaves QEMU running until SECS
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 255, "", "timed out after %s s" % timeout)


def ssh_opts():
    """ssh options for the guest: per-home known_hosts, accept-new (never with checking switched off)"""
    return ["-o", "BatchMode=yes"] + sshw().hostkey_opts() + ["-o", "ConnectTimeout=8"]


def sshw():
    """lib/sshwait.py, loaded by path (this script runs under python3 -I)"""
    return load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sshwait.py"), "sshwait")


def pid_alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False


# what a slow host needs. The guest shutdown takes as long as the host is slow (30 to 60 s on 16 threads, minutes on 6 shared ones), so the
# wait for QEMU to exit is a bound, not a time: it ends the moment QEMU is gone. SHUTDOWN_WAIT is only the point where we give up and quit over QMP.
SHUTDOWN_WAIT = 900


def guest_shutdown(ssh):
    """`shutdown -h now` in the FOREGROUND of the ssh command. the old `nohup shutdown -h now >/dev/null 2>&1 &` was killed together with
    the ssh session in 100 percent of the runs seen (the guest stayed up for 5 minutes and more), so QEMU never exited by itself and the CLI
    cut the power by QMP after its grace period, an unclean shutdown. In the foreground it prints "System shutdown time has arrived" and
    QEMU exits by itself. The connection may drop while it runs, so the result is ignored."""
    sh(ssh + ["sync; sync; shutdown -h now"], 120)


# a guest that is awake and unlocked however long the first login takes: display sleep and the screen saver lock
GUARD = ("pmset -a displaysleep 0 sleep 0 disksleep 0 2>&1; U=${LABUSER:-snap}; "
         "sudo -u $U defaults write com.apple.screensaver idleTime -int 0; sudo -u $U defaults write com.apple.screensaver askForPassword -int 0; "
         "sudo -u $U defaults -currentHost write com.apple.screensaver idleTime -int 0; echo guard-done")


def set_guard(ssh, deadline, alive, pause=5):
    """One try is not enough: the login storm makes sshd miss its banner (seen on 6 threads: the first and only try failed and the session
    locked 6 minutes after the desktop). Retry until it is set, bounded by the overall deadline."""
    while time.time() < deadline and alive():
        if "guard-done" in sh(ssh + [GUARD], 120).stdout:
            return True
        time.sleep(pause)
    return False


def session_locked(ssh):
    """True/False from the I/O registry (IOConsoleLocked), None if the guest did not answer"""
    r = sh(ssh + ["ioreg -l -n Root -d1 | grep -o '\"IOConsoleLocked\" = [A-Za-z]*'"], 30)
    if "IOConsoleLocked" not in r.stdout:
        return None
    return r.stdout.strip().endswith("Yes")


def wait_exit(rundir, grace=None):
    """the guest halts itself and QEMU exits; after the grace period ask for quit over QMP, then stop that exact PID"""
    if grace is None:
        grace = int(os.environ.get("GOLDEN_SHUTDOWN_WAIT", SHUTDOWN_WAIT))
    pid = int(open(os.path.join(rundir, "qemu.pid")).read())
    def alive():
        try:
            os.kill(pid, 0); return True
        except OSError:
            return False
    t = time.time()
    while alive() and time.time() - t < grace:
        time.sleep(2)
    if alive():
        print("guest did not power off in %d s: QMP quit" % grace, flush=True)
        try:
            s = socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect(os.path.join(rundir, "qmp.sock"))
            f = s.makefile("rw"); f.readline()
            for c in ("qmp_capabilities", "quit"):
                f.write(json.dumps({"execute": c}) + "\n"); f.flush(); f.readline()
        except Exception as e:
            print("qmp: %s" % e)
        time.sleep(5)
    if alive():
        os.kill(pid, 15)


def quiet(rundir, port, key, quietsh, timeout):
    ssh = ["ssh", "-p", str(port), "-i", key] + ssh_opts() + ["root@127.0.0.1"]
    scp = ["scp", "-P", str(port), "-i", key] + ssh_opts()
    t0 = float(open(os.path.join(rundir, "start")).read())
    res = {"ssh_s": None, "ok": False}
    deadline = time.time() + timeout
    qpid = int(open(os.path.join(rundir, "qemu.pid")).read())
    print("golden phase 2: booting the guest, waiting for ssh (2 to 4 minutes on 16 threads, longer on fewer; nothing is hung)", flush=True)
    r, _ = sshw().wait_ssh(ssh, "echo up; id -un", deadline, first_at=45, t_ref=t0, alive=lambda: pid_alive(qpid),
                           ok=lambda x: x.stdout.startswith("up"))
    if r is None and not pid_alive(qpid):
        print("QEMU exited before ssh answered")
    if r is not None:
        res["ssh_s"] = round(time.time() - t0, 1)
    if res["ssh_s"] is None:
        json.dump(res, open(os.path.join(rundir, "golden.json"), "w")); print("no ssh within %ss" % timeout); return 1
    print("ssh up at %.1f s; waiting for the login session (Dock and Finder), then applying the quiet profile (about 2 minutes)" % res["ssh_s"], flush=True)
    # first the guard. On a slow host the first login takes 7 to 11 minutes; the guest is idle meanwhile and, with the default 10 minute
    # display sleep, sleeps its display and locks the session. The guard needs no login session.
    res["guard"] = set_guard(ssh, deadline, lambda: pid_alive(qpid))
    print("stay-awake guard: %s" % ("set after %.0f s" % (time.time() - t0) if res["guard"] else "NOT SET"), flush=True)
    # desktop check: the login session exists and Dock + Finder run (autologin worked). Until the overall deadline, not for a fixed count of tries.
    while time.time() < deadline and pid_alive(qpid):
        r = sh(ssh + ["pgrep -x Dock >/dev/null && pgrep -x Finder >/dev/null && echo desk"], 20)
        if "desk" in r.stdout:
            res["desktop_s"] = round(time.time() - t0, 1); break
        time.sleep(3)
    print("desktop (Dock+Finder): %s" % res.get("desktop_s"), flush=True)
    print("applying the quiet profile, then a clean shutdown (QEMU is stopped by the CLI if the guest does not power off)", flush=True)
    r = sh(scp + [quietsh, "root@127.0.0.1:/tmp/apply-quiet.sh"], 60)
    if r.returncode != 0:
        print(r.stderr); json.dump(res, open(os.path.join(rundir, "golden.json"), "w")); return 1
    r = sh(ssh + ["sh /tmp/apply-quiet.sh 2>&1 | tail -8; "
                  "launchctl print-disabled system | grep -c '=> disabled'; "
                  "launchctl print-disabled system | grep -E 'backgroundtaskmanagementd|kernelmanagerd'"], max(300, deadline - time.time()))
    print(r.stdout, r.stderr)
    res["quiet_out"] = r.stdout[-600:]
    res["ok"] = r.returncode == 0 and "disabled entries written" in r.stdout
    res["locked"] = session_locked(ssh)
    print("session locked: %s" % res["locked"], flush=True)
    json.dump(res, open(os.path.join(rundir, "golden.json"), "w"))
    if res["ok"]:
        guest_shutdown(ssh)
        wait_exit(rundir)
    return 0 if res["ok"] else 1


WARM_HTML = ("<!doctype html><html><head><title>warm</title><style>html,body{margin:0;height:100%;background:#00ffcc}</style></head>"
             "<body><h1>snapintosh warm-up</h1></body></html>")


def warm(rundir, port, key, timeout, settle=30):
    ssh = ["ssh", "-p", str(port), "-i", key] + ssh_opts() + ["root@127.0.0.1"]
    t0 = float(open(os.path.join(rundir, "start")).read())
    qpid = int(open(os.path.join(rundir, "qemu.pid")).read())
    res = {"ok": False}
    def done():
        json.dump(res, open(os.path.join(rundir, "warm.json"), "w"))
    def alive():
        try:
            os.kill(qpid, 0); return True
        except OSError:
            return False
    print("warm-up: booting the finished golden, waiting for ssh", flush=True)
    deadline = time.time() + timeout
    r, _ = sshw().wait_ssh(ssh, "echo up", deadline, first_at=45, t_ref=t0, alive=alive, ok=lambda x: x.stdout.startswith("up"))
    if r is not None:
        res["ssh_s"] = round(time.time() - t0, 1)
    if "ssh_s" not in res:
        print("warm-up: no ssh"); done(); return 1
    while time.time() < deadline and alive():
        r = sh(ssh + ["pgrep -x Dock >/dev/null && pgrep -x Finder >/dev/null && echo desk"], 20)
        if "desk" in r.stdout:
            res["desktop_s"] = round(time.time() - t0, 1); break
        time.sleep(3)
    if "desktop_s" not in res:
        print("warm-up: no desktop"); done(); return 1
    print("warm-up: desktop at %.1f s, settling %d s, then Safari" % (res["desktop_s"], settle), flush=True)
    time.sleep(settle)
    sh(ssh + ["printf '%s' '" + WARM_HTML + "' > /Users/Shared/snapintosh-warm.html; chmod 644 /Users/Shared/snapintosh-warm.html"], 30)
    opened = load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "guestapp.py"), "guestapp").open_app(ssh, "Safari", "file:///Users/Shared/snapintosh-warm.html", log=lambda m: print("warm-up: " + m, flush=True))
    print("warm-up: Safari open %s" % ("ok" if opened["ok"] else "did not start (continuing)"), flush=True)
    t1 = time.time()
    for _ in range(60):
        r = sh(ssh + ["pgrep -f com.apple.WebKit.WebContent >/dev/null && echo wc"], 20)
        if "wc" in r.stdout:
            res["safari_s"] = round(time.time() - t1, 1); break
        time.sleep(3)
    print("warm-up: Safari WebContent %s" % ("running after %.1f s" % res["safari_s"] if "safari_s" in res else "not seen (continuing)"), flush=True)
    time.sleep(25)   # let the first page draw (about 30 s from the open command to the first drawn page on 12 vCPUs)
    sh(ssh + ["launchctl asuser 501 osascript -e 'tell application \"Safari\" to quit'"], 60)
    for _ in range(20):
        r = sh(ssh + ["pgrep -x Safari >/dev/null || echo gone"], 20)
        if "gone" in r.stdout:
            break
        time.sleep(3)
    sh(ssh + ["rm -f /Users/Shared/snapintosh-warm.html"], 30)
    res["locked"] = session_locked(ssh)
    guest_shutdown(ssh)
    res["ok"] = True; done()
    wait_exit(rundir)
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "extra":
        user, pw = "snap", os.environ.get("SNAPINTOSH_GUEST_PASSWORD", "snaplab")
        rest = a[4:]
        while rest:
            if rest[0] == "--user": user = rest[1]
            else:
                print("goldenctl extra: unknown option %s (the password comes from SNAPINTOSH_GUEST_PASSWORD)" % rest[0], file=sys.stderr); sys.exit(2)
            rest = rest[2:]
        try:
            sys.exit(extra(a[1], a[2], a[3], user, pw))
        except Exception as e:
            print("golden phase 1 failed: %s" % e, file=sys.stderr); sys.exit(1)
    if a and a[0] == "warm":
        rc = warm(a[1], a[2], a[3], int(a[4]) if len(a) > 4 else 1200)
        if rc != 0:
            try:
                os.kill(int(open(os.path.join(a[1], "qemu.pid")).read()), 15)
            except Exception:
                pass
        sys.exit(rc)
    if a and a[0] == "quiet":
        rc = quiet(a[1], a[2], a[3], a[4], int(a[5]) if len(a) > 5 else 900)
        if rc != 0:
            # nothing is left to wait for: stop QEMU, or the CLI waits for it until --secs (an hour)
            try:
                os.kill(int(open(os.path.join(a[1], "qemu.pid")).read()), 15)
            except Exception:
                pass
        sys.exit(rc)
    print(__doc__); sys.exit(2)
