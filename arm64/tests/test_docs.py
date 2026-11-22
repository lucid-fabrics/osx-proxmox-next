"""A4 #5: every number and md5 the README quotes for release-v2 and --pgo comes from the release's own record (docs/bench/r30-release-v2.txt, pins.env).
The two must agree: a README figure that is not in the record, an md5 that is not in pins.env or the record, or a measured time that differs fails."""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
readme = open(os.path.join(HERE, "README.md")).read()
pins = open(os.path.join(HERE, "pins.env")).read()
record = open(os.path.join(HERE, "docs", "bench", "r30-release-v2.txt")).read()
cli = open(os.path.join(HERE, "snapintosh")).read()
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else "  " + str(extra)))
    fails += 0 if cond else 1


def section(title):
    a = readme.index("### " + title)
    b = readme.index("\n### ", a + 4)
    return readme[a:b]


spin, pgo = section("The BQL spin (release-v2)"), section("Faster binary, opt-in")
rec_pct = set(re.findall(r"(\d+(?:\.\d+)?) ?%", record))
for name, text in (("BQL spin", spin), ("--pgo", pgo)):
    nums = sorted(set(re.findall(r"(\d+(?:\.\d+)?) ?%", text)))
    miss = [n for n in nums if n not in rec_pct]
    check("every percentage of the README's %s section is in docs/bench/r30-release-v2.txt" % name, not miss, miss)

# the headline figures are the record's, each tied to its metric and direction (a swapped or sign-flipped figure fails)
def rec(block_from, block_to, metric):
    blk = record.split(block_from)[1].split(block_to)[0] if block_to else record.split(block_from)[1]
    m = re.search(re.escape(metric) + r"\s+median [+-][\d.]+ \(([+-])([\d.]+) %\)", blk)
    return (m.group(1), m.group(2)) if m else (None, None)


def readme_says(text, pattern, metric_rec, wanted_sign):
    sign, num = metric_rec
    m = re.search(pattern, text)
    return bool(m) and sign == wanted_sign and m.group(1) == num


v2 = lambda k: rec("== V2 - REL", "== PGO - REL", k)
check("README release-v2: boot to Dock N % faster is the record's figure", readme_says(spin, r"boot to Dock ([\d.]+) % faster", v2("boot to Dock"), "-"), v2("boot to Dock"))
check("README release-v2: Safari open to first draw N % faster is the record's figure", readme_says(spin, r"Safari open to first draw ([\d.]+) % faster", v2("Safari open to draw"), "-"), v2("Safari open to draw"))
check("README release-v2: host CPU per launch N % lower is the record's figure", readme_says(spin, r"host CPU per launch ([\d.]+) % lower", v2("host CPU-s per launch"), "-"), v2("host CPU-s per launch"))
pv = lambda k: rec("== PGO - V2", None, k)
check("README --pgo against release-v2: Safari open to draw N % slower is the record's figure", readme_says(pgo, r"Safari open to draw was ([\d.]+) % slower", pv("Safari open to draw"), "+"), pv("Safari open to draw"))
check("README --pgo against release-v2: boot to Dock N % faster is the record's figure", readme_says(pgo, r"boot to Dock ([\d.]+) % faster and host CPU", pv("boot to Dock"), "-"), pv("boot to Dock"))
pr = lambda k: rec("== PGO - REL", "== PGO - V2", k)
check("README --pgo against release-v1: N % faster to the Dock is the record's figure", readme_says(pgo, r"it is ([\d.]+) % faster to the Dock", pr("boot to Dock"), "-"), pr("boot to Dock"))
check("README --pgo against release-v1: N % to draw is the record's figure", readme_says(pgo, r"Dock and ([\d.]+) % to draw", pr("Safari open to draw"), "-"), pr("Safari open to draw"))
mins = re.search(r"built in (\d+) minutes", record)
check("README's measured --pgo time is the record's", mins is not None and ("%s minutes measured" % mins.group(1)) in pgo, mins and mins.group(1))
check("the CLI's printed --pgo time is the record's too", mins is not None and ("%s min measured" % mins.group(1)) in cli, mins and mins.group(1))

# md5s: every 32-digit hex in the README is in pins.env or the record, and the pinned release-v2 md5 is the one the README gives
bad = [h for h in sorted(set(re.findall(r"\b[0-9a-f]{32}\b", readme))) if h not in pins and h not in record]
check("every md5 in the README is in pins.env or the bench record", not bad, bad)
ref = re.search(r"^# Debian 13: qemu-system-aarch64 ([0-9a-f]{32})", pins, re.M)
check("pins.env gives the release-v2 md5 and the README quotes it", ref is not None and ref.group(1) in readme, ref and ref.group(1))
check("the release-v2 pin in pins.env is the head the patches are cut from",
      re.search(r"QEMU_REF=\$\{QEMU_REF:-([0-9a-f]{40})\}", pins).group(1) in open(sorted(glob.glob(os.path.join(HERE, "patches", "qemu", "0008-*.patch")))[0]).readline())
ub = re.search(r"^# translation pack is new\. Ubuntu 26\.04 \(gcc [\d.]+\): qemu-system-aarch64 ([0-9a-f]{32})", pins, re.M)
check("pins.env records an Ubuntu 26.04 release-v2 md5 and the README quotes it as measured",
      ub is not None and ub.group(1) in readme and "has not been measured yet" not in readme, ub and ub.group(1))
sys.exit(1 if fails else 0)
