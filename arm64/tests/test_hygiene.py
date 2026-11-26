"""Tree hygiene: no references to private planning files, lab machines, plan ids or hard-coded lab paths, no em-dashes.
patches/ and docs/bench/ are records, so they are not scanned."""
import os, re, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMDASH = chr(0x2014)
PATTERNS = {
    "private plans/ reference": re.compile(r"\bplans/"),
    "hard-coded lab home": re.compile("/home/" + "snap\\b"),
    "cpu share setting": re.compile("cpu" + "units"),
    "plan id": re.compile(r"\b(?:P\d{2,3}[bc]?|(?:[RSQHGD]\d{1,2}[a-c]?|A[1-9]))\b(?= [a-z(]|[:,)])"),
    "tool or assistant name": re.compile("(?i)mini" + "max|claude|anthropic"),
    "VM id": re.compile(r"\bVM 4\d\d\b"),
    "lab host name": re.compile(r"\b(pve-\w+|tirsa|mirna|labg5)\b"),
    "em-dash": re.compile(EMDASH),
}
bad = []
for root, dirs, files in os.walk(HERE):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "patches", "bench")]
    for n in files:
        p = os.path.join(root, n)
        if n == os.path.basename(__file__) or n.endswith((".pyc", ".img", ".bin")):
            continue
        try:
            text = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.split("\n"), 1):
            for what, rx in PATTERNS.items():
                if rx.search(line):
                    bad.append("%s:%d: %s" % (os.path.relpath(p, HERE), i, what))
if bad:
    print("\n".join(bad))
    sys.exit(1)
print("hygiene ok")
