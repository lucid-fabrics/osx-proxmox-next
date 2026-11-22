"""ssh to the guest: a known_hosts file of our own with accept-new, never host key checking switched off."""
import os, re, subprocess, sys, importlib.util

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("sshwait", HERE + "/lib/sshwait.py")
sw = importlib.util.module_from_spec(spec); spec.loader.exec_module(sw)
fails = []


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        fails.append(name)


o = sw.hostkey_opts({"SNAPINTOSH_KNOWN_HOSTS": "/h/known_hosts"})
check("accept-new", "StrictHostKeyChecking=accept-new" in o and "UserKnownHostsFile=/h/known_hosts" in o, o)
o = sw.hostkey_opts({"SNAPINTOSH_KNOWN_HOSTS": "/h x/known_hosts"})
check("path-with-space-quoted", 'UserKnownHostsFile="/h x/known_hosts"' in o, o)
o = sw.hostkey_opts({"SNAPINTOSH_HOME": "/data/s"})
check("default-known-hosts-in-home", "UserKnownHostsFile=/data/s/known_hosts" in o, o)
bad = []
for f in ("snapintosh", "lib/snapctl.py", "lib/goldenctl.py", "lib/benchctl.py", "lib/sshwait.py", "lib/compact.sh"):
    t = open(os.path.join(HERE, f)).read()
    if re.search(r"StrictHostKeyChecking[= ]+(no|off)\b", t) or re.search(r"UserKnownHostsFile=/dev/null", t):
        bad.append(f)
check("no-host-key-checking-off", not bad, bad)
for f in ("lib/snapctl.py", "lib/goldenctl.py", "lib/benchctl.py"):
    check("uses-hostkey-opts-%s" % os.path.basename(f), "hostkey_opts()" in open(os.path.join(HERE, f)).read())
sys.exit(1 if fails else 0)
