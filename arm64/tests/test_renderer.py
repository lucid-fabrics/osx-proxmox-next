"""The renderer sources come from a checksummed tarball: a wrong checksum stops the build before anything is unpacked, a stale cached
file is replaced, a tarball with paths outside its trees is refused, and every clone pin must be a full commit id.
Run: python3 -I tests/test_renderer.py"""
import hashlib, os, subprocess, sys, tarfile, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(HERE, "snapintosh")
T = tempfile.mkdtemp(prefix="rend-")
fails = []


def check(name, cond, detail=""):
    print(("pass  " if cond else "FAIL  ") + name + ("" if cond else "  " + str(detail)[:600]))
    if not cond:
        fails.append(name)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def make_tar(path, extra=None):
    with tarfile.open(path, "w:gz") as t:
        for d in ("reims-vgpu", "metal2vulkan"):
            src = os.path.join(T, "stage", d, "Cargo.toml")
            write(src, "[package]\nname='x'\n")
            t.add(os.path.join(T, "stage", d), arcname=d)
        for name in (extra or []):
            info = tarfile.TarInfo(name)
            info.size = 0
            t.addfile(info)


# a cargo that only leaves the dist tree the build checks for; the other tools are present and do nothing
for tool in ("rustc", "clang", "ld.lld", "lld-link", "make", "cmake", "rustup"):
    write(T + "/bin/" + tool, "#!/bin/sh\nexit 0\n", 0o755)
write(T + "/bin/cargo", '#!/bin/sh\nmkdir -p "$CARGO_TARGET_DIR/dist-vulkan/lib/pkgconfig"; echo "$CARGO_BUILD_JOBS" > "$CARGO_TARGET_DIR/jobs"; : > "$CARGO_TARGET_DIR/dist-vulkan/lib/pkgconfig/reims-vgpu.pc"\n', 0o755)


def build(home, **env):
    e = dict(os.environ, HOME=T + "/hm", XDG_CONFIG_HOME=T + "/cfg", SNAPINTOSH_HOME=home, SNAPINTOSH_NO_REPRO="1",
             PATH=T + "/bin:" + os.environ["PATH"])
    for k in ("REIMS_REPO", "M2V_REPO", "RENDERER_URL", "RENDERER_SHA256"):
        e.pop(k, None)
    e.update(env)
    r = subprocess.run([CLI, "build", "--only", "reims"], env=e, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


good = T + "/good.tar.gz"
make_tar(good)
want = sha(good)

# 1. the right checksum builds and unpacks both trees
h1 = T + "/h1"
rc, out = build(h1, RENDERER_URL="file://" + good, RENDERER_SHA256=want)
check("matching-checksum-builds", rc == 0 and "reims-vgpu dist" in out, out)
check("both-trees-unpacked", all(os.path.isfile(h1 + "/src/%s/Cargo.toml" % d) for d in ("reims-vgpu", "metal2vulkan")), out)
check("no-git-needed-for-the-trees", not os.path.exists(h1 + "/src/reims-vgpu/.git"))
m = os.stat(h1 + "/src/reims-vgpu/Cargo.toml").st_mtime
rc, out = build(h1, RENDERER_URL="file://" + good, RENDERER_SHA256=want)
check("a-second-build-keeps-the-unpacked-tree", rc == 0 and "already unpacked" in out and os.stat(h1 + "/src/reims-vgpu/Cargo.toml").st_mtime == m, out)

# 2. a wrong checksum stops the build before anything is unpacked, and the bad file is not kept
h2 = T + "/h2"
rc, out = build(h2, RENDERER_URL="file://" + good, RENDERER_SHA256="0" * 64)
check("wrong-checksum-refuses", rc != 0 and "checksum mismatch" in out and want in out, out)
check("wrong-checksum-unpacks-nothing", not os.path.exists(h2 + "/src/reims-vgpu") and not os.path.exists(h2 + "/src/metal2vulkan"))
check("wrong-checksum-keeps-no-download", not any(n.startswith("renderer-") for n in os.listdir(h2 + "/cache")) if os.path.isdir(h2 + "/cache") else True)

# 3. a cached file that no longer matches is replaced by a fresh download
h3 = T + "/h3"
write(h3 + "/cache/renderer-%s.tar.gz" % want, "stale")
rc, out = build(h3, RENDERER_URL="file://" + good, RENDERER_SHA256=want)
check("stale-cache-is-downloaded-again", rc == 0 and "downloading it again" in out and sha(h3 + "/cache/renderer-%s.tar.gz" % want) == want, out)

# 4. a tarball with a member outside its trees is refused even when its checksum is right
bad = T + "/bad.tar.gz"
make_tar(bad, extra=["../escape"])
rc, out = build(T + "/h4", RENDERER_URL="file://" + bad, RENDERER_SHA256=sha(bad))
check("path-outside-the-trees-is-refused", rc != 0 and "outside" in out and not os.path.exists(T + "/escape"), out)
bad2 = T + "/bad2.tar.gz"
make_tar(bad2, extra=["/abs/escape"])
rc, out = build(T + "/h5", RENDERER_URL="file://" + bad2, RENDERER_SHA256=sha(bad2))
check("absolute-path-is-refused", rc != 0 and "outside" in out, out)

# 5. no download and no repository: a malformed or missing checksum is refused
rc, out = build(T + "/h6", RENDERER_URL="file://" + good, RENDERER_SHA256="abc")
check("short-checksum-refused", rc != 0 and "RENDERER_SHA256" in out, out)
rc, out = build(T + "/h7", RENDERER_URL="file://" + T + "/missing.tar.gz", RENDERER_SHA256=want)
check("failed-download-stops-the-build", rc != 0 and "download of" in out, out)

# 6. clone pins must be full commit ids, and a tree whose origin was another repository is re-pointed
g = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
repo = T + "/repo"
os.makedirs(repo)
subprocess.run(["git", "-C", repo, "init", "-q"], check=True, env=g)
write(repo + "/f", "x")
subprocess.run(["git", "-C", repo, "add", "f"], check=True, env=g)
subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "chore(test): add a file"], check=True, env=g)
full = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
for label, ref in (("abbreviation", full[:12]), ("branch-name", "master"), ("tag-name", "v1")):
    rc, out = build(T + "/hc", REIMS_REPO="file://" + repo, REIMS_REF=ref, M2V_REPO="file://" + repo, M2V_REF=full)
    check("clone-pin-%s-refused" % label, rc != 0 and "full 40 digit commit id" in out, out)
h8 = T + "/h8"
rc, out = build(h8, REIMS_REPO="file://" + repo, REIMS_REF=full, M2V_REPO="file://" + repo, M2V_REF=full)
check("clone-pin-full-id-builds", rc == 0, out)
repo2 = T + "/repo2"
subprocess.run(["git", "clone", "-q", repo, repo2], check=True, env=g)
rc, out = build(h8, REIMS_REPO="file://" + repo2, REIMS_REF=full, M2V_REPO="file://" + repo2, M2V_REF=full)
origin = subprocess.run(["git", "-C", h8 + "/src/reims-vgpu", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
check("clone-pin-repoints-origin", rc == 0 and origin == "file://" + repo2, origin + out)

# 7. cargo gets one job per 2.5 GB of RAM (an 8 GB runner was OOM-killed with six), at most one per thread, and the environment wins
def jobs_for(**env):
    h = tempfile.mkdtemp(prefix="jobs-", dir=T)
    rc, out = build(h, RENDERER_URL="file://" + good, RENDERER_SHA256=want, **env)
    p = h + "/build/reims-target/jobs"
    return rc, (open(p).read().strip() if os.path.exists(p) else out)


r8 = jobs_for(SNAPINTOSH_CHECK_MEM_KB=str(8137000))
check("8gib-runner-gets-3-jobs", r8 == (0, "3"), r8)
check("tiny-host-gets-1-job", jobs_for(SNAPINTOSH_CHECK_MEM_KB=str(1000000)) == (0, "1"))
nproc = os.cpu_count() or 1
check("big-host-is-capped-at-the-threads", jobs_for(SNAPINTOSH_CHECK_MEM_KB=str(2621440 * (nproc + 50))) == (0, str(nproc)))
check("cargo-build-jobs-from-the-environment-wins", jobs_for(SNAPINTOSH_CHECK_MEM_KB=str(8137000), CARGO_BUILD_JOBS="5") == (0, "5"))

print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
