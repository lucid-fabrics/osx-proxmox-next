"""Guest app helpers: the one place that opens an app in the guest and asks whether it is on screen.

`ssh ... "launchctl asuser 501 open -a Safari URL"` blocked for 90 s in 7 of 12 settles of a soak run on a 6 thread host (and failed with
255 now and then) while the guest was busy with its login session. The open is therefore started detached on the guest (the remote shell
returns at once, so ssh does too, and the open survives the end of the ssh session) and verified by looking for the process, with a bounded retry. A saved snapshot of an app that never drew
is no use, so `ready` tells whether the app has a window on screen (CoreGraphics window list, no extra permission needed) and, for Safari,
whether its WebContent and GPU processes have stayed up (processes killed and restarted in a loop are the slow-host Safari problem).

Load it by path (callers run under python3 -I): importlib.util.spec_from_file_location."""
import json, re, shlex, subprocess, time

USER_UID = 501
WEBCONTENT = "com.apple.WebKit.WebContent"
WEBGPU = "com.apple.WebKit.GPU"   # killed after 3 s when it is slow to start, which takes every WebContent process down with it


def detached_open(app, url=""):
    """the remote command: `open` in the user's session, in a session of its own (perl setsid) and in the background, so the ssh that
    carries it ends at once. The setsid matters: a plain `nohup sh -c ... &` is killed when the ssh session ends, before `open` has done
    anything (tried on the guest: Safari never started; with setsid it did)"""
    args = ["launchctl", "asuser", str(USER_UID), "open", "-a", app] + ([url] if url else [])
    return "perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' %s >/dev/null 2>&1 </dev/null &" % " ".join(shlex.quote(a) for a in args)


def remote(ssh, cmd, timeout, run=subprocess.run):
    """one ssh command, bounded: (returncode, stdout); 124 when it did not return in time"""
    try:
        r = run(ssh + [cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or ""
    except subprocess.TimeoutExpired:
        return 124, ""


def remote_retry(ssh, cmd, timeout, conn_tries=4, pause=3.0, run=subprocess.run, sleep=time.sleep):
    """remote(), asking again (bounded) while ssh itself failed to connect (rc 255)"""
    rc, out = 255, ""
    for i in range(conn_tries):
        rc, out = remote(ssh, cmd, timeout, run=run)
        if rc != 255:
            break
        sleep(pause)
    return rc, out


def is_running(ssh, app, timeout=20, run=subprocess.run, sleep=time.sleep):
    rc, out = remote_retry(ssh, "pgrep -x %s >/dev/null && echo up" % shlex.quote(app), timeout, run=run, sleep=sleep)
    return "up" in out


def open_app(ssh, app, url="", verify_s=60.0, tries=3, per_call=20, poll=3.0, log=None, run=subprocess.run, clock=time.time, sleep=time.sleep):
    """open APP (with URL) in the guest and return as soon as its process exists. Returns {"ok", "tries", "seconds"}.
    Each try is a detached open (see above) and then a look for the process every POLL seconds for VERIFY_S; a try that
    shows no process is made again, TRIES times at most. The bound is the clock, not the call count: the verify loop looks at the clock only after
    an is_running call, and every ssh call may itself be asked again 4 times (4 * (PER_CALL + 3) s), so the worst case is about
    TRIES * (VERIFY_S + 2 * 4 * (PER_CALL + 3)) s (with the defaults 3 * (60 + 184) s, about 12 minutes), not TRIES * (VERIFY_S + PER_CALL)."""
    say = log or (lambda m: None)
    t0 = clock()
    for n in range(1, tries + 1):
        rc, _ = remote_retry(ssh, detached_open(app, url), per_call, run=run, sleep=sleep)
        if rc != 0:
            say("open %s: try %d, the ssh command ended with status %s" % (app, n, rc))
        end = clock() + verify_s
        while True:
            if is_running(ssh, app, per_call, run=run, sleep=sleep):
                say("open %s: running %.0f s after the first try (try %d)" % (app, clock() - t0, n))
                return {"ok": True, "tries": n, "seconds": round(clock() - t0, 1)}
            if clock() >= end:
                break
            sleep(poll)
        say("open %s: no process after %.0f s on try %d%s" % (app, verify_s, n, ", asking again" if n < tries else ", giving up"))
    return {"ok": False, "tries": tries, "seconds": round(clock() - t0, 1)}


def _jxa(app, min_px):
    """osascript (JavaScript) that prints how many on-screen, normal-layer windows of APP are at least MIN_PX in both directions"""
    return ('ObjC.import("CoreGraphics");'
            'var l=ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly,0)));'
            'l.filter(function(w){return w.kCGWindowOwnerName==%s&&w.kCGWindowLayer==0&&w.kCGWindowBounds.Width>=%d&&w.kCGWindowBounds.Height>=%d}).length'
            % (json.dumps(app), min_px, min_px))


def windows_on_screen(ssh, app, min_px=200, timeout=40, run=subprocess.run, sleep=time.sleep):
    """number of on-screen windows of APP, or None when the guest could not tell (osascript failed or answered nonsense)"""
    cmd = "launchctl asuser %d osascript -l JavaScript -e %s" % (USER_UID, shlex.quote(_jxa(app, min_px)))
    rc, out = remote_retry(ssh, cmd, timeout, run=run, sleep=sleep)
    m = re.search(r"^\s*(\d+)\s*$", out, re.M)
    return int(m.group(1)) if rc == 0 and m else None


def etime_seconds(s):
    """ps etime [[dd-]hh:]mm:ss to seconds, None when it does not parse"""
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", s.strip())
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def youngest_age(ssh, pattern, timeout=20, run=subprocess.run, sleep=time.sleep):
    """age in seconds of the youngest process whose command line contains PATTERN; None when there is none or ps failed"""
    rc, out = remote_retry(ssh, "ps -axo etime=,command= | grep -F %s | grep -v grep" % shlex.quote(pattern), timeout, run=run, sleep=sleep)
    ages = [etime_seconds(l.split(None, 1)[0]) for l in out.splitlines() if l.strip()]
    ages = [a for a in ages if a is not None]
    return min(ages) if rc == 0 and ages else None


def ready(ssh, app, stable_s=15, run=subprocess.run, sleep=time.sleep):
    """is APP on screen? Returns (state, detail), state "ready", "not-ready" or "unknown" (the guest could not tell, say so, never guess).
    A window counts; for Safari the WebContent process must also have run for STABLE_S seconds, because a page process that is killed and
    restarted over and over (the slow-host WebKit watchdog) shows a window with 'a problem repeatedly occurred' in it."""
    n = windows_on_screen(ssh, app, run=run, sleep=sleep)
    if n is None:
        return "unknown", "could not ask the guest for %s's windows" % app
    if n == 0:
        return "not-ready", "no %s window on screen" % app
    if app == "Safari":
        age = youngest_age(ssh, WEBCONTENT, run=run, sleep=sleep)
        if age is None:
            return "not-ready", "Safari has a window but no page process (WebContent) is running"
        if age < stable_s:
            return "not-ready", "Safari's page process is only %d s old (it is being restarted)" % age
        gpu = youngest_age(ssh, WEBGPU, run=run, sleep=sleep)   # none at all is fine (a build without one); a young one is the Safari kill loop
        if gpu is not None and gpu < stable_s:
            return "not-ready", "Safari's GPU process is only %d s old (WebKit's 3 s watchdog is still killing it)" % gpu
    return "ready", "%d window(s) on screen" % n


class OpenJob:
    """open_app in a thread, with the poll() of a Popen (None while running, 0 when the app came up, 1 when it did not)"""

    def __init__(self, ssh, app, url="", **kw):
        import threading
        self.result = None
        self._t = threading.Thread(target=lambda: setattr(self, "result", open_app(ssh, app, url, **kw)), daemon=True)
        self._t.start()

    def poll(self):
        if self._t.is_alive():
            return None
        return 0 if self.result and self.result["ok"] else 1

    def wait(self, timeout=None):
        self._t.join(timeout)
        return self.poll()
