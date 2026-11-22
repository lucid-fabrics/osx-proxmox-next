"""Lint for the exit code rules of `snapintosh`: nothing may read $? after an `if`/`fi`, and every call of `launch` must be
followed by a look at LAUNCH_RC (launch itself returns 0 so that set -e stays on inside it; its verdict is LAUNCH_RC)."""
import os, re, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
lines = open(os.path.join(HERE, "snapintosh")).read().split("\n")
bad = []
for i, l in enumerate(lines):
    s = l.strip()
    if re.match(r"rc=\$\?", s) and i and lines[i - 1].strip() == "fi":
        bad.append("%d: rc=$? straight after fi (always 0)" % (i + 1))
    if re.search(r"\blaunch\s*(\|\||&&|\|)", s) or re.search(r"(if|!|until|while)\s+launch\b", s):
        bad.append("%d: launch in a condition (turns set -e off inside it)" % (i + 1))
    if re.search(r"(^|; )launch(;|$)", s) and not s.startswith("launch()"):
        window = " ".join(x.strip() for x in lines[i:i + 3])
        if "LAUNCH_RC" not in window:
            bad.append("%d: launch without a LAUNCH_RC check" % (i + 1))
# R23 rules that close whole classes of bug
text = "\n".join(lines)
if not re.search(r"^shopt -u patsub_replacement", text, re.M):
    bad.append("patsub_replacement is not switched off: `&` in a replacement is the match under bash 5.2+")
for i, l in enumerate(lines):
    if l.lstrip().startswith("#"):
        continue
    if re.search(r"\$\{[A-Za-z_]+(\[[@*]\])?//", l) and "missing=${missing//" not in l and not re.search(r'//"\$|//\$\{?[A-Z]+\\/|//"', l):
        bad.append("%d: ${var//pattern/replacement} with an unquoted pattern: %s" % (i + 1, l.strip()))
    if re.search(r'kill\s+"?\$\(cat', l) or "CL_PIDFILES" in l:
        bad.append("%d: a pid read from a file is killed without checking the process (use kill_nbd or track): %s" % (i + 1, l.strip()))
    if re.search(r"CL_PIDS\+=", l) and not l.startswith("track()"):
        bad.append("%d: register processes with `track PID` (it keeps the start time), not CL_PIDS+=" % (i + 1))
    if re.search(r"^\s*trap\s+cleanup_all\s+EXIT\s+INT", l):
        bad.append("%d: cl_trap installs the INT/TERM handlers that exit with 128+signal" % (i + 1))
def at(pat, start=0):
    m = re.compile(pat, re.M).search(text, start)
    return m.start() if m else -1
# the helper and tail of a launch are tracked, so the trap stops them
for what in ('track "$hp"', 'track "$tp"', 'track "$QP"', 'track "$mh"'):
    if what not in text:
        bad.append("launch does not register %s with the cleanup trap" % what)
# a golden's phase 2 overlay is installed only after the verdict; a refused golden keeps the ssh pin; a stale nbd.pid is removed before the trap
a_verdict, a_mv = at(r"^  local rc=0$", at(r"^launch\(\)")), at(r'mv "\$R/root.qcow2" "\$VM/golden/root-quiet.qcow2"')
if not (0 < a_verdict < a_mv):
    bad.append("golden phase 2: root-quiet.qcow2 is installed before the verdict")
a_refuse, a_rm, a_trap = at(r'exists: move the golden directory away'), at(r'^  rm -f "\$KNOWN_HOSTS"   # a new golden'), at(r"^  cl_trap$", at(r"^make_disk_golden\(\)"))
if not (0 < a_refuse < a_rm):
    bad.append("make-disk golden removes known_hosts before it refuses an existing golden")
if not (0 < at(r'^  rm -f "\$g/tmp/nbd.pid"') < a_trap):
    bad.append("make-disk golden: the stale nbd.pid is not removed before the trap")
if bad:
    print("\n".join(bad))
    sys.exit(1)
print("rc lint ok")
