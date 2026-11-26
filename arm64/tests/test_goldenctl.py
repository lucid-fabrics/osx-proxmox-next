"""golden phase 1: the user name is validated and quoted into the guest root shell, the password comes from the environment."""
import importlib.util, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("goldenctl", HERE + "/lib/goldenctl.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
T = tempfile.mkdtemp(prefix="sng-")
fails = []


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        fails.append(name)


for u in ("snap", "a", "_x", "user-1", "u_2", "a" * 32):
    check("user-ok-%s" % u[:8], g.valid_user(u))
for u in ("", "1a", "-a", "A", "a b", "a;b", "a$(x)", "a`x`", "a\nb", "a'b", 'a"b', "a" * 33, "a/b", None):
    check("user-bad-%r" % (u,), not g.valid_user(u))

# a fake helper directory: records what would be typed into the guest
os.makedirs(T + "/helper"); os.makedirs(T + "/guest")
open(T + "/guest/golden-phase1.sh", "w").write("#!/bin/sh\n")
open(T + "/helper/persist-sshd.py", "w").write('''
import json, os
def rec(*a): open(os.environ["PS_LOG"], "a").write(json.dumps([x if isinstance(x, str) else x.hex() for x in a]) + "\\n")
def discover_stage(sp): return "/var/root/stage dir"
def upload(sp, stage, name, data, mode): rec("upload", name, data)
def run_serial(sp, cmd, timeout=0):
    rec("run", cmd)
    return "golden phase 1 done"
''')
os.environ["PS_LOG"] = T + "/log"
g.extra("sock", T + "/helper", T + "/guest", "snap", "pw")
log = [json.loads(x) for x in open(T + "/log").read().split("\n") if x]
runs = [x[1] for x in log if x[0] == "run"]
check("user-and-stage-quoted", runs and runs[0] == "AUTOLOGIN_USER=snap '/var/root/stage dir/golden-phase1.sh'", runs)
kc = [x[2] for x in log if x[0] == "upload" and x[1] == "kcpassword"]
check("password-uploaded-as-kcpassword", kc and kc[0] == g.kcpassword("pw").hex())
try:
    g.extra("sock", T + "/helper", T + "/guest", "a;touch /tmp/x", "pw")
    check("bad-user-refused", False)
except ValueError:
    check("bad-user-refused", True)

# command line: the password is not an option any more, the user is checked
def run(args, env=None):
    return subprocess.run([sys.executable, "-I", HERE + "/lib/goldenctl.py", "extra", "s", T + "/helper", T + "/guest"] + args,
                          capture_output=True, text=True, env=dict(os.environ, **(env or {})))
r = run(["--password", "x"])
check("password-option-gone", r.returncode == 2 and "SNAPINTOSH_GUEST_PASSWORD" in r.stderr, r.stderr)
r = run(["--user", "a b"])
check("bad-user-on-cli-fails", r.returncode == 1 and "invalid user" in r.stderr, r.stderr)
subprocess.run(["rm", "-rf", T])
sys.exit(1 if fails else 0)
