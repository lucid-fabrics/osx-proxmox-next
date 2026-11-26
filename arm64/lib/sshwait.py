"""Patient ssh readiness wait.

A booting macOS guest answers ssh late and slowly: while the login session starts all vCPUs are busy and the sshd banner can
take 10 to 30 s. A try that is killed after 6 to 25 s abandons a login that is still in progress, and the guest then spends its
scarce CPU on the next sshd-session and sshd-auth. A measurement showed 7 of 14 boots at 95-108 s with short tries against 0 of 14 with
one patient try, and under host load a login after 21-27 s against none in 420 s. So: one connection attempt in flight, started
at first_at seconds after t_ref, ConnectTimeout 120, and a try is never killed before the overall deadline.

Load it by path (callers run under python3 -I): importlib.util.spec_from_file_location."""
import os, subprocess, time


def opt(key, value):
    """one `-o key=value` pair; a value with white space is quoted the way ssh_config wants it"""
    return ["-o", '%s="%s"' % (key, value) if any(c.isspace() for c in value) else "%s=%s" % (key, value)]


def known_hosts(env=os.environ):
    """the per-home known_hosts file of the guest's host key (snapintosh exports SNAPINTOSH_KNOWN_HOSTS)"""
    kh = env.get("SNAPINTOSH_KNOWN_HOSTS")
    if kh:
        return kh
    home = env.get("SNAPINTOSH_HOME") or os.path.join(env.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "snapintosh")
    return os.path.join(home, "known_hosts")


def hostkey_opts(env=os.environ):
    """ssh options that trust the guest's host key on first use and pin it afterwards, in a file of our own: never
    with checking switched off and never the user's ~/.ssh/known_hosts (the guest at 127.0.0.1:PORT is not a host they know)"""
    return ["-o", "StrictHostKeyChecking=accept-new"] + opt("UserKnownHostsFile", known_hosts(env))


def without_connect_timeout(base):
    """base with every `-o ConnectTimeout=N` pair removed"""
    out, i = [], 0
    while i < len(base):
        if base[i] == "-o" and i + 1 < len(base) and base[i + 1].lower().startswith("connecttimeout="):
            i += 2
            continue
        out.append(base[i]); i += 1
    return out


def wait_ssh(base, cmd, deadline, first_at=0.0, t_ref=None, interval=2.0, connect_timeout=120, alive=None,
             ok=None, clock=time.time, sleep=time.sleep, run=subprocess.run):
    """Run `base + [cmd]` until it succeeds. Returns (CompletedProcess, tries) on success, (None, tries) when the absolute
    time `deadline` passes or alive() turns false. The first try starts at t_ref + first_at (t_ref defaults to now). The
    command is run one at a time; a try is killed only at the deadline, never earlier. ok(result) may veto a rc 0 answer."""
    argv = without_connect_timeout(base)
    argv = argv[:1] + ["-o", "ConnectTimeout=%d" % connect_timeout] + argv[1:] + [cmd]
    start = (clock() if t_ref is None else t_ref) + first_at
    tries = 0
    while True:
        if alive is not None and not alive():
            return None, tries
        now = clock()
        if now >= deadline:
            return None, tries
        if now < start:
            sleep(min(interval, start - now, max(deadline - now, 0)))
            continue
        tries += 1
        try:
            r = run(argv, capture_output=True, text=True, timeout=max(deadline - now, 1))
            if r.returncode == 0 and (ok is None or ok(r)):
                return r, tries
        except subprocess.TimeoutExpired:
            return None, tries   # the deadline killed it
        sleep(min(interval, max(deadline - clock(), 0)))
