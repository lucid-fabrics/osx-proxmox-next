#!/usr/bin/env python3
"""pgoctl.py: the checks of `snapintosh build --pgo`.

  check-log LOG      read the compile log of the profile-use build and refuse (exit 1) when the profile does not match the source:
                     any missing-profile or coverage warning raised while compiling an object of accel/tcg, target/arm or hw/vmapple
                     (a missing .gcda file, a function without a profile, a checksum or counter mismatch), and any coverage-mismatch
                     warning anywhere. A missing .gcda for an object the instrumented build never linked (trace files for other targets,
                     stubs, tests, subprojects) is expected and only counted.
  check-prof DIR     read the profile directory the training run wrote: exit 1 unless it holds .gcda files for accel/tcg, target/arm
                     and hw/vmapple.
"""
import os
import re
import sys

HOT = re.compile(r"^(accel_tcg|target_arm|hw_vmapple)_")
SKIP_DIRS = ("libqemuutil", "libstubs", "tools", "tests", "subprojects", "contrib", "storage-daemon", "qemu-")
STATUS = re.compile(r"^\[\d+/\d+\]\s+(?:Compiling|Linking)\b.*?\s(\S+)\s*$")


def is_hot(obj):
    """True for the object of a source file in accel/tcg, target/arm or hw/vmapple that the QEMU binary is made of."""
    if not obj:
        return False
    d, _, base = obj.rpartition("/")
    if d.startswith(SKIP_DIRS):
        return False
    return bool(HOT.match(base))


def classify(line):
    """kind of profile warning on LINE: 'file', 'function', 'mismatch' or None"""
    if "profile count data file not found" in line:
        return "file"
    if "not found in profile data" in line:
        return "function"
    if "Wcoverage-mismatch" in line or "coverage mismatch" in line or "control flow checksum" in line \
            or "number of counters" in line or "profile data may be" in line or "profile data is out of date" in line \
            or "corrupted profile info" in line:
        return "mismatch"
    return None


def scan(lines):
    cur = None
    counts = {"file": 0, "function": 0, "mismatch": 0}
    bad = []
    for raw in lines:
        line = raw.rstrip("\n")
        m = STATUS.match(line)
        if m:
            cur = m.group(1)
            continue
        kind = classify(line)
        if kind is None:
            continue
        counts[kind] += 1
        if kind == "mismatch" or is_hot(cur):
            bad.append((cur, kind, line.strip()[:200]))
    return counts, bad


def check_log(path):
    with open(path, errors="replace") as f:
        counts, bad = scan(f)
    print("pgo profile check: %d missing .gcda files (objects the instrumented build did not link), %d function-level, %d coverage mismatch warnings"
          % (counts["file"], counts["function"], counts["mismatch"]))
    if counts["file"] == 0 and not bad:
        print("pgo profile check: FAILED, the log holds no profile warnings at all: they were switched off, so nothing was checked", file=sys.stderr)
        return 1
    if bad:
        print("pgo profile check: FAILED, the profile does not match this source and compiler:", file=sys.stderr)
        for obj, kind, line in bad[:10]:
            print("  %s [%s] %s" % (obj or "?", kind, line), file=sys.stderr)
        if len(bad) > 10:
            print("  ... and %d more" % (len(bad) - 10), file=sys.stderr)
        return 1
    return 0


def check_prof(d):
    try:
        names = [n for n in os.listdir(d) if n.endswith(".gcda")]
    except OSError as e:
        print("pgo: no profile directory: %s" % e, file=sys.stderr)
        return 1
    missing = [g for g in ("accel_tcg", "target_arm", "hw_vmapple") if not any(g in n for n in names)]
    print("pgo: %d .gcda files in %s" % (len(names), d))
    if missing:
        print("pgo: the training run wrote no profile for: %s" % ", ".join(missing), file=sys.stderr)
        return 1
    return 0


def main(argv):
    if len(argv) == 2 and argv[0] == "check-log":
        return check_log(argv[1])
    if len(argv) == 2 and argv[0] == "check-prof":
        return check_prof(argv[1])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
