#!/usr/bin/env python3
"""NullMoth driver releases, the payload disk and the guest lookup. Standard library only.

The Python package imports this module and the bash installer embeds it verbatim (tests/test_nullmoth_parity.py
diffs the two), so there is one implementation. NullMoth is never pinned: the newest published release is the
default and the user may pick any older one. A release is only used when its tarball matches the SHA256SUMS file
of the release and, when GitHub stores one, the sha256 digest of the asset. There is no silent fallback.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

try:
    from . import igpu_engine as _gpu
except ImportError:  # run as a script from the bash installer: both engines sit in one folder
    import igpu_engine as _gpu

REPO = "nullmoth/nvidia-macos-driver"
GITHUB_API = "https://api.github.com/repos/" + REPO + "/"
DOWNLOAD_PREFIX = "https://github.com/" + REPO + "/releases/download/"
LATEST = "latest"
TARBALL_RE = re.compile(r"^nullmoth-nvidia-.+\.tar\.gz$")
SUMS_NAME = "SHA256SUMS.txt"
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# file names end up inside root shell scripts and paths: plain characters only
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
FW_URL_RE = re.compile(r"^https://download\.nvidia\.com/XFree86/Linux-x86_64/[0-9][0-9.]*/[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# The installer fetches NVIDIA's GPU firmware from this host; the host fetches it first so the guest needs no network.
NVIDIA_HOST = "https://download.nvidia.com/"
FW_SCRIPT = "fw_fetch.sh"
PAYLOAD_LABEL = _gpu.PAYLOAD_LABEL
LAUNCHD_PLIST = "com.osxproxmoxnext.nullmoth-firstboot.plist"
MB = 1024 * 1024
PAYLOAD_MIN_MB = 64
PAYLOAD_SLACK_MB = 32
RESULT_FINAL = ("verified", "failed:")
SWEEP_MAX_HOSTS = 1024
SWEEP_BATCH = 64


class ReleaseError(Exception):
    """GitHub could not be asked, or a release cannot be used. The message says what to do next."""


def data_dir():
    here = Path(__file__).resolve().parent
    return here / "data" / "nullmoth" if (here / "data" / "nullmoth").is_dir() else here


# ── releases ─────────────────────────────────────────────────────────

def github_get(url):
    """The JSON GitHub returns for *url*. GITHUB_TOKEN, when set, raises the rate limit."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "osx-proxmox-next"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise ReleaseError("GitHub refused the request (rate limit reached). Wait a few minutes and run the "
                               "command again, or set GITHUB_TOKEN to a GitHub token to raise the limit.") from exc
        if exc.code == 404:
            raise ReleaseError("GitHub has no such release.") from exc
        raise ReleaseError("GitHub answered HTTP %d for %s. Try again in a few minutes." % (exc.code, url)) from exc
    except (OSError, ValueError) as exc:
        raise ReleaseError("Could not reach GitHub (%s). Check the network and run the command again." % exc) from exc


def _read_url(url, limit=1 << 20):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "osx-proxmox-next"}),  # noqa: S310
                                    timeout=30) as resp:
            return resp.read(limit).decode("utf-8", "replace")
    except (OSError, ValueError) as exc:
        raise ReleaseError("Could not download %s (%s)." % (url, exc)) from exc


def _pick(rel):
    """The usable data of one GitHub release, or None when it has no NullMoth tarball with a SHA256SUMS file."""
    assets = rel.get("assets") or []
    tarballs = [a for a in assets if TARBALL_RE.match(a.get("name") or "")]
    sums = [a for a in assets if a.get("name") == SUMS_NAME]
    if len(tarballs) != 1 or len(sums) != 1:
        return None
    tar = tarballs[0]
    m = DIGEST_RE.match(tar.get("digest") or "")
    tag = rel.get("tag_name") or ""
    if not TAG_RE.match(tag) or not NAME_RE.match(tar["name"]):
        return None
    return {"tag": tag, "version": tag[1:] if tag[:1] in "vV" else tag, "name": tar["name"],
            "url": tar.get("browser_download_url") or "", "sums_url": sums[0].get("browser_download_url") or "",
            "digest": m.group(1) if m else "", "size": int(tar.get("size") or 0),
            "prerelease": bool(rel.get("prerelease")), "published": rel.get("published_at") or ""}


def list_releases(get=github_get):
    """Every published release that carries the driver tarball and its SHA256SUMS, newest first."""
    data = get(GITHUB_API + "releases?per_page=30")
    picks = [_pick(r) for r in data if not r.get("draft")]
    return sorted((p for p in picks if p), key=lambda p: p["published"], reverse=True)


def _usable(pick):
    for key in ("url", "sums_url"):
        if not pick[key].startswith(DOWNLOAD_PREFIX):
            raise ReleaseError("NullMoth %s points at an unexpected download address, refused." % pick["tag"])
    return pick


def resolve(choice=LATEST, get=github_get):
    """The release picked by *choice* ("latest" or a tag), with `source` set to latest or chosen."""
    choice = choice or LATEST
    if choice == LATEST:
        found = [p for p in list_releases(get) if not p["prerelease"]]
        if not found:
            raise ReleaseError("No published NullMoth release has the driver tarball this tool needs.")
        return dict(_usable(found[0]), source=LATEST)
    if not TAG_RE.match(choice):
        raise ReleaseError("%r is not a release tag." % choice)
    want = choice.lstrip("vV")
    for pick in list_releases(get):
        if pick["tag"] == choice or pick["version"] == want:
            return dict(_usable(pick), source="chosen")
    try:
        pick = _pick(get(GITHUB_API + "releases/tags/" + choice))
    except ReleaseError as exc:
        raise ReleaseError("NullMoth has no release tagged %s (%s). See the list with: nullmoth-versions" % (choice, exc)) from exc
    if pick is None:
        raise ReleaseError("NullMoth %s has no driver tarball this tool can use." % choice)
    return dict(_usable(pick), source="chosen")


def parse_sums(text, name):
    """The sha256 listed for *name* in a SHA256SUMS file ('' when it is not listed). Every line must be well formed."""
    found = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([0-9a-fA-F]{64}) [ *](.+)$", line.strip())
        if not m:
            raise ReleaseError("The SHA256SUMS file of the release is malformed, refused.")
        if m.group(2) == name:
            found = m.group(1).lower()
    return found


def expected_sha256(pick, read=_read_url):
    """The sha256 the tarball of *pick* must have: from SHA256SUMS, and equal to the GitHub asset digest when there is one."""
    listed = parse_sums(read(pick["sums_url"]), pick["name"])
    if not listed:
        raise ReleaseError("SHA256SUMS of NullMoth %s does not list %s, so the download cannot be verified and is "
                           "refused." % (pick["tag"], pick["name"]))
    if pick["digest"] and pick["digest"] != listed:
        raise ReleaseError("SHA256SUMS and the GitHub digest of NullMoth %s disagree, refused." % pick["tag"])
    return listed


def sha256_file(path):
    return _gpu.sha256_file(path)


def download(url, dest, sha256, prefix=""):
    """Download *url* to *dest* unless a file with *sha256* is already there. Raises ReleaseError on a mismatch."""
    dest = Path(dest)
    if prefix and not url.startswith(prefix):
        raise ReleaseError("Refusing to download from %s." % url)
    if dest.exists() and sha256_file(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "osx-proxmox-next"}),  # noqa: S310
                                    timeout=60) as resp, open(part, "wb") as out:
            shutil.copyfileobj(resp, out)
    except (OSError, ValueError) as exc:
        part.unlink(missing_ok=True)
        raise ReleaseError("Could not download %s (%s)." % (url, exc)) from exc
    got = sha256_file(part)
    if got != sha256:
        part.unlink()
        raise ReleaseError("Checksum mismatch for %s: expected %s, got %s. The file was deleted." % (url, sha256, got))
    part.replace(dest)
    return dest


def fetch_release(pick, dest_dir, read=_read_url, fetch=download):
    """Download and verify the tarball of *pick* into *dest_dir*; returns (path, sha256)."""
    sha = expected_sha256(pick, read)
    return fetch(pick["url"], Path(dest_dir) / pick["name"], sha, DOWNLOAD_PREFIX), sha


# ── GPU firmware (NVIDIA's own package, read by the driver's installer) ──

def _member_text(tarball, suffix):
    with tarfile.open(tarball, "r:gz") as tf:
        for member in tf:
            if member.isfile() and member.name.lstrip("./").endswith(suffix) and member.size < 1 << 20:
                handle = tf.extractfile(member)
                return handle.read().decode("utf-8", "replace") if handle else ""
    return ""


def firmware_pins(tarball):
    """{version, url, sha256, name} the release's fw_fetch.sh pins for NVIDIA's driver package, or None."""
    try:
        text = _member_text(tarball, "/" + FW_SCRIPT)
    except (OSError, tarfile.TarError):
        return None
    ver = re.search(r'^VER=([0-9][0-9.]*)\s*$', text, re.M)
    sha = re.search(r'^SHA_RUN=([0-9a-f]{64})\s*$', text, re.M)
    url = re.search(r'^URL_RUN="([^"]+)"\s*$', text, re.M)
    if not (ver and sha and url):
        return None
    address = url.group(1).replace("$VER", ver.group(1))
    if not FW_URL_RE.match(address):
        return None
    name = address.rsplit("/", 1)[1]
    if not NAME_RE.match(name) or ".." in name:
        return None
    return {"version": ver.group(1), "url": address, "sha256": sha.group(1), "name": name}


def fetch_firmware(pins, dest_dir, fetch=download):
    return fetch(pins["url"], Path(dest_dir) / pins["name"], pins["sha256"], NVIDIA_HOST)


# ── payload disk ─────────────────────────────────────────────────────

def render(name, values):
    """A payload script with its @PLACEHOLDERS@ filled in."""
    text = (data_dir() / name).read_text()
    for key, value in values.items():
        text = text.replace("@%s@" % key, value)
    return text


def payload_files(tarball, tar_sha, firmware=None, fw_sha=""):
    """name on the payload disk -> ("copy", path) or ("text", content)."""
    values = {"TARBALL": Path(tarball).name, "TARBALL_SHA256": tar_sha,
              "FW_RUN": Path(firmware).name if firmware else "", "FW_RUN_SHA256": fw_sha if firmware else ""}
    files = {"stage.sh": ("text", render("nm_stage.sh", values)),
             "firstboot.sh": ("text", render("nm_firstboot.sh", values)),
             LAUNCHD_PLIST: ("text", render("nm_launchd.plist", values)),
             Path(tarball).name: ("copy", str(tarball))}
    if firmware:
        files[Path(firmware).name] = ("copy", str(firmware))
    return files


def payload_mb(files):
    total = sum(os.path.getsize(v) if k == "copy" else len(v.encode()) for k, v in files.values())
    return max(PAYLOAD_MIN_MB, total // MB + 1 + (total // MB) // 50 + PAYLOAD_SLACK_MB)


def build_payload(out, files, run=_gpu.default_run):
    """A GPT disk with one FAT32 partition (label OSXPNIGPU) holding *files*. Returns (ok, message)."""
    out = Path(out)
    try:
        size_mb = payload_mb(files)
    except OSError as exc:
        return False, "could not read a payload file: %s" % exc
    with open(out, "wb") as f:
        f.truncate(size_mb * MB)
    for argv in (["sgdisk", "-Z", str(out)],
                 ["sgdisk", "-n", "1:0:0", "-t", "1:0700", "-c", "1:" + PAYLOAD_LABEL, str(out)]):
        code, msg = run(argv, 60)
        if code != 0:
            return False, "sgdisk failed: " + msg.strip()
    loop, part = _gpu._attach_loop(out, run)
    if not loop:
        return False, part
    code, msg = run(["mkfs.vfat", "-F", "32", "-n", PAYLOAD_LABEL, part], 120)
    if code != 0:
        run(["losetup", "-d", loop], 20)
        return False, "mkfs.vfat failed: " + msg.strip()
    mnt = tempfile.mkdtemp(prefix="nullmoth-payload-")
    code, msg = run(["mount", part, mnt], 60)
    if code != 0:
        os.rmdir(mnt)
        run(["losetup", "-d", loop], 20)
        return False, "could not mount the payload image: " + msg.strip()
    try:
        for name, (kind, value) in files.items():
            if kind == "copy":
                shutil.copyfile(value, os.path.join(mnt, name))
            else:
                Path(mnt, name).write_text(value)
    except OSError as exc:
        return False, "could not fill the payload image: %s" % exc
    finally:
        run(["sync"], 300)
        _gpu._release(mnt, loop, run)
    return True, "payload disk built (%d MB)" % size_mb


def read_result(img, run=_gpu.default_run):
    """The guest's last report from the payload disk. The guest writes around the host's page cache, so the cached
    blocks of the disk are dropped first: without it a report that changed after an earlier read is not seen."""
    run(["blockdev", "--flushbufs", str(img)], 20)
    return _gpu.read_result(img, run)


def parse_result(text):
    """{state, detail, kexts, display, ip} from the guest's last line. state is verified, failed, installed or ''."""
    text = (text or "").strip()
    out = {"state": "", "detail": "", "kexts": 0, "display": "", "ip": ""}
    if text in ("installed", "verifying"):
        out["state"] = text
    elif text.startswith("failed:"):
        # text from the guest is shown to the user: plain printable characters only, no backslash escapes
        out.update(state="failed", detail=re.sub(r"[^\x20-\x7e]|\\", "", text[len("failed:"):]).strip()[:300])
    elif text.startswith("verified"):
        out["state"] = "verified"
        for key, value in re.findall(r"\b(kexts|display|ip)=(\S+)", text):
            if key == "kexts":
                out[key] = int(value) if value.isdigit() else 0
            elif key == "display":
                out[key] = value if value in ("yes", "no") else ""
            else:
                out[key] = value if IPV4_RE.match(value) else ""
    return out


def is_final(text):
    return (text or "").startswith(RESULT_FINAL)


# ── the guest from the host ──────────────────────────────────────────

def vm_mac(cfg):
    """The MAC address of net0 from a `qm config` mapping (model=MAC,bridge=...), lower case, or ''."""
    m = re.match(r"^[a-z0-9_-]+=((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})", cfg.get("net0", ""))
    return m.group(1).lower() if m else ""


def parse_neighbours(text, mac):
    """The IPv4 address the neighbour table lists for *mac* (`ip -4 neigh show` output), or ''."""
    for line in text.splitlines():
        parts = line.split()
        if "lladdr" in parts and parts[parts.index("lladdr") + 1].lower() == mac and parts[-1] not in ("FAILED", "INCOMPLETE"):
            return parts[0]
    return ""


def _sweep(run):
    """Ping every address of the host's subnets once so the neighbour table learns the guest."""
    import ipaddress
    code, out = run(["ip", "-4", "-o", "addr", "show", "scope", "global"], 10)
    procs = []

    def reap():
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.SubprocessError:
                p.kill()
        del procs[:]
    for line in out.splitlines() if code == 0 else []:
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", line)
        if not m or "vmbr" not in line.split()[1]:
            continue
        iface = ipaddress.ip_interface(m.group(1))
        net = iface.network
        if net.num_addresses > SWEEP_MAX_HOSTS:
            continue
        for ip in net.hosts():
            if ip == iface.ip:
                continue
            try:
                procs.append(subprocess.Popen(["ping", "-c", "1", "-W", "1", str(ip)],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            except OSError:
                reap()
                return
            if len(procs) >= SWEEP_BATCH:
                reap()
    reap()


def find_guest_ip(mac, run=_gpu.default_run, sweep=_sweep):
    """The guest's IPv4 address from the host's neighbour table, sweeping the bridge subnets once when it is not there."""
    if not mac:
        return ""

    def lookup():
        code, out = run(["ip", "-4", "neigh", "show"], 10)
        return parse_neighbours(out, mac) if code == 0 else ""
    ip = lookup()
    if ip:
        return ip
    sweep(run)
    return lookup()


def port_open(ip, port, timeout=3):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── command line (used by the bash installer) ────────────────────────

def _emit(obj):
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _usage():
    sys.stderr.write("usage: nullmoth_engine.py releases|resolve <latest|tag>|fetch <latest|tag> <dir>|fw-pins <tarball>|"
                     "fw-fetch <tarball> <dir>|payload <img> <tarball> <sha256> [firmware sha256]|result <img>|"
                     "parse-result <text>|vm-mac|find-ip <mac>|port <ip> <port>\n")
    return 64


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "releases":
            _emit({"releases": list_releases()})
        elif cmd == "resolve" and len(argv) == 3:
            _emit(resolve(argv[2]))
        elif cmd == "fetch" and len(argv) == 4:
            pick = resolve(argv[2])
            path, sha = fetch_release(pick, argv[3])
            _emit(dict(pick, path=str(path), sha256=sha))
        elif cmd == "fw-pins" and len(argv) == 3:
            _emit(firmware_pins(argv[2]) or {})
        elif cmd == "fw-fetch" and len(argv) == 4:
            pins = firmware_pins(argv[2])
            if pins is None:
                sys.stderr.write("the release pins no firmware package\n")
                return 1
            _emit(dict(pins, path=str(fetch_firmware(pins, argv[3]))))
        elif cmd == "payload" and len(argv) in (5, 7):
            fw = argv[5] if len(argv) == 7 else None
            ok, msg = build_payload(argv[2], payload_files(argv[3], argv[4], fw, argv[6] if fw else ""))
            sys.stderr.write(msg + "\n")
            return 0 if ok else 1
        elif cmd == "result" and len(argv) == 3:
            sys.stdout.write(read_result(argv[2]) + "\n")
        elif cmd == "parse-result" and len(argv) == 3:
            _emit(parse_result(argv[2]))
        elif cmd == "vm-mac":
            cfg = dict(line.split(": ", 1) for line in sys.stdin.read().splitlines() if ": " in line)
            sys.stdout.write(vm_mac(cfg) + "\n")
        elif cmd == "find-ip" and len(argv) == 3:
            sys.stdout.write(find_guest_ip(argv[2].lower()) + "\n")
        elif cmd == "port" and len(argv) == 4:
            return 0 if port_open(argv[2], int(argv[3])) else 1
        else:
            return _usage()
    except (ReleaseError, OSError) as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
