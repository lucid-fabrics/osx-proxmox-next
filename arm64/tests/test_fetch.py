"""fetch: a failed or partial download is a non-zero exit, size and hash are verified, a --url without hash says so, few retries."""
import hashlib, http.server, os, re, subprocess, sys, tempfile, threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = HERE + "/snapintosh"
BODY = bytes(range(256)) * 4
SHA = hashlib.sha256(BODY).hexdigest()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def head(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()

    def do_HEAD(self):
        self.head()

    def do_GET(self):
        self.head()
        if self.path.startswith("/short"):
            self.wfile.write(BODY[:400])      # announces 1024 bytes, sends 400, closes
        else:
            self.wfile.write(BODY)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
T = tempfile.mkdtemp(prefix="snf-")
fails = []


def fetch(args, pins=None, name="o"):
    cfg = T + "/cfg-" + name
    os.makedirs(cfg + "/snapintosh", exist_ok=True)
    open(cfg + "/snapintosh/pins.env", "w").write(pins or "")
    env = dict(os.environ, SNAPINTOSH_HOME=T + "/home", XDG_CONFIG_HOME=cfg, SNAPINTOSH_FETCH_ATTEMPTS="2", SNAPINTOSH_FETCH_SLEEP="0")
    out = T + "/dl-" + name
    r = subprocess.run([CLI, "fetch", "--out", out] + args, env=env, capture_output=True, text=True, timeout=120)
    return r, out


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else " " + str(detail)))
    if not cond:
        fails.append(name)


U = "http://127.0.0.1:%d/ok.ipsw" % PORT
r, _ = fetch(["--url", U, "--sha256", SHA], name="a")
check("url-with-hash-ok", r.returncode == 0 and "sha256 matches" in r.stdout, r.stdout + r.stderr)
r, out = fetch(["--url", U], name="b")
check("url-without-hash-checks-size-and-says-so", r.returncode == 0 and "size 1024 matches" in r.stdout and "no hash" in r.stdout, r.stdout + r.stderr)
r, _ = fetch(["--url", U, "--sha256", "0" * 64], name="c")
check("url-wrong-hash-fails", r.returncode != 0, r.stdout)
r, _ = fetch(["--url", U, "--sha256", "zz"], name="d")
check("bad-hash-argument-fails", r.returncode != 0)
r, out = fetch(["--url", "http://127.0.0.1:%d/short.ipsw" % PORT], name="e")
check("partial-download-fails", r.returncode != 0, "rc=%s %s" % (r.returncode, r.stdout))
check("partial-download-no-success-line", "[ok]" not in r.stdout)
r, _ = fetch(["--url", "http://127.0.0.1:1/none.ipsw"], name="f")
check("unreachable-fails", r.returncode != 0)
pins = "IPSW_26A428_URL=%s\nIPSW_26A428_SIZE=%d\nIPSW_26A428_SHA256=%s\n"
r, _ = fetch([], pins % (U, len(BODY), SHA), name="g")
check("pinned-ok", r.returncode == 0 and "pinned build" in r.stdout, r.stdout + r.stderr)
r, _ = fetch([], pins % (U, len(BODY), "1" * 64), name="h")
check("pinned-wrong-hash-fails", r.returncode != 0, r.stdout)
r, _ = fetch([], pins % (U, len(BODY) - 1, SHA), name="i")
check("pinned-wrong-size-fails", r.returncode != 0, r.stdout)
r, _ = fetch([], pins % ("http://127.0.0.1:%d/short.ipsw" % PORT, len(BODY), SHA), name="j")
check("pinned-partial-fails", r.returncode != 0, r.stdout)
n = int(re.search(r"SNAPINTOSH_FETCH_ATTEMPTS:-(\d+)", open(CLI).read()).group(1))
check("sane-default-retries", 1 <= n <= 10, n)
subprocess.run(["rm", "-rf", T])
sys.exit(1 if fails else 0)
