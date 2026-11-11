"""nullmoth_engine: releases, verified downloads, firmware pins, payload disk, result parsing, guest lookup."""
from __future__ import annotations

import hashlib
import io
import json
import socket
import tarfile
import urllib.error
from pathlib import Path

import pytest

from osx_proxmox_next import nullmoth_engine as eng

BASE = eng.DOWNLOAD_PREFIX


def rel(tag, published, pre=False, draft=False, digest=True, sums=True, tarballs=1, host=BASE):
    sha = hashlib.sha256(tag.encode()).hexdigest()
    assets = [{"name": f"nullmoth-nvidia-{tag[1:]}.tar.gz" if i == 0 else f"nullmoth-nvidia-{tag[1:]}-b.tar.gz",
               "browser_download_url": f"{host}{tag}/nullmoth-nvidia-{tag[1:]}.tar.gz",
               "digest": f"sha256:{sha}" if digest else None, "size": 7} for i in range(tarballs)]
    if sums:
        assets.append({"name": "SHA256SUMS.txt", "browser_download_url": f"{host}{tag}/SHA256SUMS.txt"})
    return {"tag_name": tag, "prerelease": pre, "draft": draft, "published_at": published, "assets": assets}


RELEASES = [rel("v2.8", "2026-10-01T00:00:00Z"), rel("v2.9", "2026-11-01T00:00:00Z"),
            rel("v3.0-rc1", "2026-11-05T00:00:00Z", pre=True), rel("v2.7", "2026-09-01T00:00:00Z", draft=True),
            rel("v2.6", "2026-08-01T00:00:00Z", sums=False), rel("v2.5", "2026-07-01T00:00:00Z", tarballs=2)]


def get(url):
    if url.endswith("releases?per_page=30"):
        return RELEASES
    raise eng.ReleaseError("GitHub has no such release.")


def test_list_is_newest_first_and_drops_drafts_and_unusable_releases():
    tags = [r["tag"] for r in eng.list_releases(get)]
    assert tags == ["v3.0-rc1", "v2.9", "v2.8"]


def test_latest_is_the_newest_stable_release():
    pick = eng.resolve(get=get)
    assert (pick["tag"], pick["source"], pick["version"]) == ("v2.9", "latest", "2.9")
    assert pick["digest"] == hashlib.sha256(b"v2.9").hexdigest()
    assert eng.resolve("", get)["tag"] == "v2.9"


def test_only_prereleases_means_no_latest():
    with pytest.raises(eng.ReleaseError, match="No published NullMoth release"):
        eng.resolve(get=lambda url: [rel("v3.0-rc1", "2026-11-05T00:00:00Z", pre=True)])


@pytest.mark.parametrize("choice", ["v2.8", "2.8"])
def test_a_tag_is_found_with_or_without_the_v(choice):
    pick = eng.resolve(choice, get)
    assert pick["tag"] == "v2.8" and pick["source"] == "chosen"


def test_a_tag_outside_the_first_page_is_asked_for_by_tag():
    def g(url):
        if url.endswith("releases?per_page=30"):
            return []
        assert url.endswith("releases/tags/v1.0")
        return rel("v1.0", "2025-01-01T00:00:00Z")
    assert eng.resolve("v1.0", g)["tag"] == "v1.0"


def test_unknown_and_unusable_tags_are_refused():
    with pytest.raises(eng.ReleaseError, match="no release tagged v9.9"):
        eng.resolve("v9.9", get)
    with pytest.raises(eng.ReleaseError, match="not a release tag"):
        eng.resolve("../../x", get)
    with pytest.raises(eng.ReleaseError, match="no driver tarball"):
        eng.resolve("v1.0", lambda url: [] if url.endswith("30") else rel("v1.0", "x", sums=False))


def test_a_download_address_outside_the_project_is_refused():
    bad = [rel("v2.9", "2026-11-01T00:00:00Z", host="https://evil.example/")]
    with pytest.raises(eng.ReleaseError, match="unexpected download address"):
        eng.resolve(get=lambda url: bad)


SUMS = ("%s  nullmoth-nvidia-2.9.tar.gz\n%s  nullmoth-nvidia-2.9.tar.gz.sig\n\n" % ("a" * 64, "b" * 64))


def test_sums_parsing():
    assert eng.parse_sums(SUMS, "nullmoth-nvidia-2.9.tar.gz") == "a" * 64
    assert eng.parse_sums(SUMS, "other") == ""
    with pytest.raises(eng.ReleaseError, match="malformed"):
        eng.parse_sums("not a sums file\n", "x")


def pick(digest="a" * 64):
    return {"tag": "v2.9", "name": "nullmoth-nvidia-2.9.tar.gz", "url": BASE + "v2.9/nullmoth-nvidia-2.9.tar.gz",
            "sums_url": BASE + "v2.9/SHA256SUMS.txt", "digest": digest}


def test_expected_sha_needs_the_listing_and_agrees_with_the_digest():
    assert eng.expected_sha256(pick(), lambda url: SUMS) == "a" * 64
    assert eng.expected_sha256(pick(""), lambda url: SUMS) == "a" * 64
    with pytest.raises(eng.ReleaseError, match="disagree"):
        eng.expected_sha256(pick("c" * 64), lambda url: SUMS)
    with pytest.raises(eng.ReleaseError, match="does not list"):
        eng.expected_sha256(pick(), lambda url: "%s  other.tar.gz\n" % ("a" * 64))


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_verifies_caches_and_deletes_a_bad_file(tmp_path, monkeypatch):
    data = b"driver bytes"
    sha = hashlib.sha256(data).hexdigest()
    calls = []
    monkeypatch.setattr(eng.urllib.request, "urlopen", lambda req, timeout=0: calls.append(req.full_url) or Resp(data))
    dest = tmp_path / "sub" / "t.tar.gz"
    assert eng.download(BASE + "x", dest, sha, BASE) == dest and dest.read_bytes() == data
    assert eng.download(BASE + "x", dest, sha, BASE) == dest and len(calls) == 1  # cached: not fetched again
    bad = tmp_path / "bad.tar.gz"
    with pytest.raises(eng.ReleaseError, match="Checksum mismatch"):
        eng.download(BASE + "x", bad, "0" * 64, BASE)
    assert not bad.exists() and not (tmp_path / "bad.tar.gz.part").exists()
    with pytest.raises(eng.ReleaseError, match="Refusing"):
        eng.download("https://evil.example/x", tmp_path / "e", sha, BASE)


def test_download_network_errors_become_release_errors(tmp_path, monkeypatch):
    def boom(req, timeout=0):
        raise OSError("down")
    monkeypatch.setattr(eng.urllib.request, "urlopen", boom)
    with pytest.raises(eng.ReleaseError, match="Could not download"):
        eng.download(BASE + "x", tmp_path / "t", "0" * 64)
    assert not (tmp_path / "t.part").exists()
    with pytest.raises(eng.ReleaseError, match="Could not download"):
        eng._read_url(BASE + "x")


def test_fetch_release_returns_the_verified_path(tmp_path):
    seen = {}

    def fetch(url, dest, sha, prefix):
        seen.update(url=url, dest=dest, sha=sha, prefix=prefix)
        return dest
    path, sha = eng.fetch_release(pick(), tmp_path, lambda url: SUMS, fetch)
    assert sha == "a" * 64 and path == tmp_path / "nullmoth-nvidia-2.9.tar.gz" and seen["prefix"] == BASE


def test_read_url_returns_text(monkeypatch):
    monkeypatch.setattr(eng.urllib.request, "urlopen", lambda req, timeout=0: Resp(b"hello"))
    assert eng._read_url(BASE + "x") == "hello"


def test_github_get_decodes_and_maps_errors(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    seen = {}

    def ok(req, timeout=0):
        seen["auth"] = req.get_header("Authorization")
        return Resp(b'[{"a": 1}]')
    monkeypatch.setattr(eng.urllib.request, "urlopen", ok)
    assert eng.github_get("https://api.github.com/x") == [{"a": 1}] and seen["auth"] == "Bearer t"

    def http(code):
        def raiser(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return raiser
    for code, text in ((403, "rate limit"), (429, "rate limit"), (404, "no such release"), (500, "HTTP 500")):
        monkeypatch.setattr(eng.urllib.request, "urlopen", http(code))
        with pytest.raises(eng.ReleaseError, match=text):
            eng.github_get("https://api.github.com/x")
    monkeypatch.setattr(eng.urllib.request, "urlopen", lambda req, timeout=0: Resp(b"not json"))
    with pytest.raises(eng.ReleaseError, match="Could not reach GitHub"):
        eng.github_get("https://api.github.com/x")


FW_SCRIPT = '''#!/bin/bash
VER=620.70.06
URL_RUN="https://download.nvidia.com/XFree86/Linux-x86_64/$VER/NVIDIA-Linux-x86_64-$VER.run"
SHA_RUN=29118e3abd08c07559bcca3d9c76238d039490af0362a2e5bcfcbe7204afd4b1
'''


def make_tarball(path: Path, fw_script: str | None = FW_SCRIPT) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, body in (("pkgroot/install.sh", "#!/bin/bash\n"), ("pkgroot/fw_fetch.sh", fw_script)):
            if body is None:
                continue
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body.encode()))
    return path


def test_firmware_pins_come_from_the_release(tmp_path):
    pins = eng.firmware_pins(make_tarball(tmp_path / "a.tar.gz"))
    assert pins == {"version": "620.70.06", "name": "NVIDIA-Linux-x86_64-620.70.06.run",
                    "sha256": "29118e3abd08c07559bcca3d9c76238d039490af0362a2e5bcfcbe7204afd4b1",
                    "url": "https://download.nvidia.com/XFree86/Linux-x86_64/620.70.06/NVIDIA-Linux-x86_64-620.70.06.run"}


@pytest.mark.parametrize("script", [
    None, "VER=1\n", FW_SCRIPT.replace("download.nvidia.com", "evil.example"),
    FW_SCRIPT.replace("$VER/NVIDIA-Linux-x86_64-$VER", "$OTHER/x"),
])
def test_firmware_pins_are_refused_when_missing_or_off_host(tmp_path, script):
    assert eng.firmware_pins(make_tarball(tmp_path / "a.tar.gz", script)) is None


def test_firmware_pins_of_an_unreadable_tarball(tmp_path):
    (tmp_path / "bad").write_bytes(b"not a tar")
    assert eng.firmware_pins(tmp_path / "bad") is None
    assert eng.firmware_pins(tmp_path / "missing") is None


def test_fetch_firmware_uses_the_nvidia_prefix(tmp_path):
    seen = {}
    pins = eng.firmware_pins(make_tarball(tmp_path / "a.tar.gz"))
    out = eng.fetch_firmware(pins, tmp_path, lambda url, dest, sha, prefix: seen.update(p=prefix, d=dest) or dest)
    assert seen["p"] == eng.NVIDIA_HOST and out.name == pins["name"]


def test_payload_files_fill_in_the_placeholders(tmp_path):
    tar = tmp_path / "nullmoth-nvidia-2.9.tar.gz"
    tar.write_bytes(b"t")
    files = eng.payload_files(tar, "a" * 64)
    assert set(files) == {"stage.sh", "firstboot.sh", eng.LAUNCHD_PLIST, tar.name}
    assert 'TARBALL="nullmoth-nvidia-2.9.tar.gz"' in files["firstboot.sh"][1]
    assert 'WANT_SHA256="%s"' % ("a" * 64) in files["firstboot.sh"][1]
    assert 'FW_RUN=""' in files["stage.sh"][1] and "@TARBALL@" not in files["stage.sh"][1]
    fw = tmp_path / "NVIDIA-Linux-x86_64-620.70.06.run"
    fw.write_bytes(b"f")
    withfw = eng.payload_files(tar, "a" * 64, fw, "b" * 64)
    assert fw.name in withfw and 'FW_RUN="%s"' % fw.name in withfw["firstboot.sh"][1]
    assert "@TARBALL@" not in withfw["firstboot.sh"][1] and "@FW_RUN_SHA256@" not in withfw["firstboot.sh"][1]


def test_payload_size_grows_with_the_files(tmp_path):
    small = tmp_path / "s"
    small.write_bytes(b"x")
    big = tmp_path / "b"
    with open(big, "wb") as f:
        f.truncate(700 * eng.MB)
    assert eng.payload_mb({"s": ("copy", str(small))}) == eng.PAYLOAD_MIN_MB
    assert eng.payload_mb({"b": ("copy", str(big)), "t": ("text", "x")}) >= 700 + eng.PAYLOAD_SLACK_MB


class Host:
    """A fake of the commands the payload builder runs: loop device, partition, mkfs, mount and unmount."""

    def __init__(self, fail=""):
        self.fail, self.calls, self.mnt = fail, [], None

    def __call__(self, argv, timeout=20):
        self.calls.append(argv)
        cmd = argv[0]
        if cmd == self.fail:
            return 1, "boom"
        if cmd == "losetup" and argv[1] == "-fP":
            return 0, "/dev/loop7\n"
        if cmd == "mount":
            self.mnt = argv[-1]
        if cmd == "umount":
            for child in Path(argv[-1]).iterdir():
                child.unlink()
        return 0, ""


def files_for(tmp_path):
    tar = tmp_path / "t.tar.gz"
    tar.write_bytes(b"tarball")
    return {"stage.sh": ("text", "echo hi\n"), tar.name: ("copy", str(tar))}


def test_the_payload_disk_is_built(tmp_path):
    run = Host()
    ok, msg = eng.build_payload(tmp_path / "p.img", files_for(tmp_path), run)
    assert ok and "payload disk built" in msg
    assert (tmp_path / "p.img").stat().st_size == eng.PAYLOAD_MIN_MB * eng.MB
    cmds = [c[0] for c in run.calls]
    assert cmds.index("mkfs.vfat") < cmds.index("mount") < cmds.index("umount")
    assert ["mkfs.vfat", "-F", "32", "-n", eng.PAYLOAD_LABEL] == run.calls[cmds.index("mkfs.vfat")][:5]


@pytest.mark.parametrize("failing,text", [("sgdisk", "sgdisk failed"), ("mkfs.vfat", "mkfs.vfat failed"),
                                           ("mount", "could not mount"), ("losetup", "could not attach")])
def test_payload_build_failures(tmp_path, failing, text):
    ok, msg = eng.build_payload(tmp_path / "p.img", files_for(tmp_path), Host(failing))
    assert not ok and text in msg


def test_a_payload_that_cannot_be_filled_is_reported(tmp_path, monkeypatch):
    files = {"x": ("copy", str(tmp_path / "missing"))}
    ok, msg = eng.build_payload(tmp_path / "p.img", files, Host())
    assert not ok and "could not read a payload file" in msg
    # a file that disappears while the disk is filled
    real = tmp_path / "real"
    real.write_bytes(b"x")
    real_copy = eng.shutil.copyfile

    def gone(src, dst):
        raise OSError("vanished")
    monkeypatch.setattr(eng.shutil, "copyfile", gone)
    ok, msg = eng.build_payload(tmp_path / "p2.img", {"x": ("copy", str(real))}, Host())
    monkeypatch.setattr(eng.shutil, "copyfile", real_copy)
    assert not ok and "could not fill" in msg


def test_results_parse():
    assert eng.parse_result("installed")["state"] == "installed"
    failed = eng.parse_result("failed: the driver installer stopped: STOP")
    assert failed["state"] == "failed" and failed["detail"] == "the driver installer stopped: STOP"
    ok = eng.parse_result("verified: kexts=5 display=no ip=10.0.0.9")
    assert (ok["state"], ok["kexts"], ok["display"], ok["ip"]) == ("verified", 5, "no", "10.0.0.9")
    assert eng.parse_result("verified: kexts=x")["kexts"] == 0
    assert eng.parse_result("started")["state"] == "" and eng.parse_result(None)["state"] == ""
    assert eng.is_final("verified: kexts=4") and eng.is_final("failed: x")
    assert not eng.is_final("installed") and not eng.is_final("")


def test_vm_mac_and_neighbours():
    assert eng.vm_mac({"net0": "vmxnet3=BC:24:11:AA:BB:CC,bridge=vmbr0,firewall=0"}) == "bc:24:11:aa:bb:cc"
    assert eng.vm_mac({"net0": "bridge=vmbr0"}) == "" and eng.vm_mac({}) == ""
    table = ("10.0.0.5 dev vmbr0 lladdr bc:24:11:aa:bb:cc REACHABLE\n10.0.0.6 dev vmbr0 lladdr bc:24:11:aa:bb:dd STALE\n"
             "10.0.0.7 dev vmbr0  FAILED\n10.0.0.8 dev vmbr0 lladdr 11:11:11:11:11:11 FAILED\n")
    assert eng.parse_neighbours(table, "bc:24:11:aa:bb:cc") == "10.0.0.5"
    assert eng.parse_neighbours(table, "bc:24:11:aa:bb:dd") == "10.0.0.6"
    assert eng.parse_neighbours(table, "11:11:11:11:11:11") == "" and eng.parse_neighbours("", "x") == ""


def test_the_guest_is_found_after_one_sweep():
    tables = ["", "10.0.0.5 dev vmbr0 lladdr bc:24:11:aa:bb:cc REACHABLE\n"]
    swept = []

    def run(argv, timeout=20):
        return 0, tables.pop(0) if tables else ""
    assert eng.find_guest_ip("bc:24:11:aa:bb:cc", run, lambda r: swept.append(1)) == "10.0.0.5" and swept == [1]
    assert eng.find_guest_ip("", run, lambda r: None) == ""
    # never found: one sweep, then give up
    swept.clear()
    assert eng.find_guest_ip("bc:24:11:aa:bb:cc", lambda argv, timeout=20: (1, ""), lambda r: swept.append(1)) == ""
    assert swept == [1]


def test_the_sweep_pings_the_bridge_subnet_only(monkeypatch):
    pinged = []

    class P:
        def __init__(self, argv, **kw):
            pinged.append(argv[-1])

        def wait(self, timeout=0):
            return 0
    monkeypatch.setattr(eng.subprocess, "Popen", P)
    out = ("2: vmbr0    inet 10.0.0.1/30 scope global vmbr0\n3: eno1    inet 192.168.9.2/24 scope global eno1\n"
           "4: vmbr1    inet 10.8.0.1/8 scope global vmbr1\n")
    eng._sweep(lambda argv, timeout=20: (0, out))
    assert pinged == ["10.0.0.2"]  # eno1 is not a bridge, vmbr1 is too large
    pinged.clear()
    eng._sweep(lambda argv, timeout=20: (1, ""))
    assert pinged == []


def test_the_sweep_survives_ping_failures(monkeypatch):
    class Slow:
        killed = False

        def __init__(self, argv, **kw):
            pass

        def wait(self, timeout=0):
            raise eng.subprocess.TimeoutExpired("ping", 1)

        def kill(self):
            Slow.killed = True
    monkeypatch.setattr(eng.subprocess, "Popen", Slow)
    eng._sweep(lambda argv, timeout=20: (0, "2: vmbr0    inet 10.0.0.1/30 scope global vmbr0\n"))
    assert Slow.killed

    def no_ping(argv, **kw):
        raise OSError("no ping")
    monkeypatch.setattr(eng.subprocess, "Popen", no_ping)
    eng._sweep(lambda argv, timeout=20: (0, "2: vmbr0    inet 10.0.0.1/30 scope global vmbr0\n"))


def test_read_result_drops_the_cached_blocks_before_reading(monkeypatch):
    order = []
    monkeypatch.setattr(eng._gpu, "read_result", lambda img, run: order.append("read") or "staged " + img)
    assert eng.read_result("/dev/x", lambda argv, timeout=20: order.append(argv) or (0, "")) == "staged /dev/x"
    assert order == [["blockdev", "--flushbufs", "/dev/x"], "read"]


def test_port_probe():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert eng.port_open("127.0.0.1", port)
    finally:
        srv.close()
    assert not eng.port_open("127.0.0.1", port, timeout=0.2)


def test_data_dir_is_the_package_data_folder():
    assert (eng.data_dir() / "nm_firstboot.sh").is_file()


def run_main(capsys, *argv):
    code = eng.main(["nullmoth_engine.py", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_commands(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "github_get", get)
    monkeypatch.setattr(eng, "list_releases", lambda g=get: [{"tag": "v2.9"}])
    code, out, _ = run_main(capsys, "releases")
    assert code == 0 and json.loads(out) == {"releases": [{"tag": "v2.9"}]}
    monkeypatch.setattr(eng, "resolve", lambda choice, g=get: {"tag": choice, "name": "n", "url": "u", "sums_url": "s"})
    assert json.loads(run_main(capsys, "resolve", "v2.9")[1])["tag"] == "v2.9"
    monkeypatch.setattr(eng, "fetch_release", lambda p, d: (Path(d) / "t.tar.gz", "a" * 64))
    fetched = json.loads(run_main(capsys, "fetch", "latest", str(tmp_path))[1])
    assert fetched["sha256"] == "a" * 64 and fetched["path"].endswith("t.tar.gz")
    tar = make_tarball(tmp_path / "a.tar.gz")
    assert json.loads(run_main(capsys, "fw-pins", str(tar))[1])["version"] == "620.70.06"
    monkeypatch.setattr(eng, "fetch_firmware", lambda pins, d: Path(d) / pins["name"])
    assert json.loads(run_main(capsys, "fw-fetch", str(tar), str(tmp_path))[1])["path"].endswith(".run")
    bare = make_tarball(tmp_path / "b.tar.gz", None)
    assert run_main(capsys, "fw-fetch", str(bare), str(tmp_path))[0] == 1
    assert json.loads(run_main(capsys, "fw-pins", str(bare))[1]) == {}


def test_cli_payload_result_and_lookups(capsys, monkeypatch, tmp_path):
    built = []
    monkeypatch.setattr(eng, "build_payload", lambda out, files: built.append((out, sorted(files))) or (True, "built"))
    tar = tmp_path / "nullmoth-nvidia-2.9.tar.gz"
    tar.write_bytes(b"t")
    fw = tmp_path / "fw.run"
    fw.write_bytes(b"f")
    assert run_main(capsys, "payload", str(tmp_path / "p.img"), str(tar), "a" * 64)[0] == 0
    assert run_main(capsys, "payload", str(tmp_path / "p.img"), str(tar), "a" * 64, str(fw), "b" * 64)[0] == 0
    assert "fw.run" not in built[0][1] and "fw.run" in built[1][1]
    monkeypatch.setattr(eng, "build_payload", lambda out, files: (False, "no space"))
    code, _out, err = run_main(capsys, "payload", str(tmp_path / "p.img"), str(tar), "a" * 64)
    assert code == 1 and "no space" in err
    monkeypatch.setattr(eng, "read_result", lambda img: "verified: kexts=4")
    assert run_main(capsys, "result", "/dev/x")[1] == "verified: kexts=4\n"
    assert json.loads(run_main(capsys, "parse-result", "installed")[1])["state"] == "installed"
    monkeypatch.setattr(eng.sys, "stdin", io.StringIO("net0: vmxnet3=BC:24:11:AA:BB:CC,bridge=vmbr0\nname: x\n"))
    assert run_main(capsys, "vm-mac")[1] == "bc:24:11:aa:bb:cc\n"
    monkeypatch.setattr(eng, "find_guest_ip", lambda mac: "10.0.0.5")
    assert run_main(capsys, "find-ip", "BC:24:11:AA:BB:CC")[1] == "10.0.0.5\n"
    monkeypatch.setattr(eng, "port_open", lambda ip, port: port == 22)
    assert run_main(capsys, "port", "10.0.0.5", "22")[0] == 0 and run_main(capsys, "port", "10.0.0.5", "5900")[0] == 1


def test_cli_errors_and_usage(capsys, monkeypatch):
    def boom(g=get):
        raise eng.ReleaseError("offline")
    monkeypatch.setattr(eng, "list_releases", boom)
    code, _out, err = run_main(capsys, "releases")
    assert code == 1 and "ERROR: offline" in err
    code, _out, err = run_main(capsys, "nonsense")
    assert code == 64 and "usage" in err
    assert run_main(capsys)[0] == 64


def test_names_that_could_reach_a_shell_are_refused():
    evil_name = rel("v2.9", "2026-11-01T00:00:00Z")
    evil_name["assets"][0]["name"] = "nullmoth-nvidia-`id`.tar.gz"
    assert eng._pick(evil_name) is None
    assert eng._pick(rel("v2.9;x", "2026-11-01T00:00:00Z")) is None
    with pytest.raises(eng.ReleaseError, match="No published NullMoth release"):
        eng.resolve(get=lambda url: [evil_name])


@pytest.mark.parametrize("name", ["..", "a`b`.run", "a b.run", "-x.run", ".hidden.run", "a/b.run"])
def test_firmware_names_are_plain_file_names(tmp_path, name):
    script = FW_SCRIPT.replace('NVIDIA-Linux-x86_64-$VER.run"', name + '"') if "/" not in name else FW_SCRIPT.replace(
        "NVIDIA-Linux-x86_64-$VER.run", name)
    assert eng.firmware_pins(make_tarball(tmp_path / "a.tar.gz", script)) is None


def test_guest_text_is_cleaned_before_it_is_shown():
    failed = eng.parse_result("failed: bad \\033[31m\x1b[2J text\x00 \\n")
    assert "\\" not in failed["detail"] and "\x1b" not in failed["detail"] and "\x00" not in failed["detail"]
    assert len(eng.parse_result("failed: " + "x" * 900)["detail"]) == 300
    ok = eng.parse_result("verified: kexts=abc display=maybe ip=not-an-ip")
    assert (ok["kexts"], ok["display"], ok["ip"]) == (0, "", "")
    assert eng.parse_result("verifying")["state"] == "verifying" and not eng.is_final("verifying")


def test_the_sweep_pings_in_batches(monkeypatch):
    live = {"now": 0, "peak": 0}

    class P:
        def __init__(self, argv, **kw):
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])

        def wait(self, timeout=0):
            live["now"] -= 1
    monkeypatch.setattr(eng.subprocess, "Popen", P)
    eng._sweep(lambda argv, timeout=20: (0, "2: vmbr0    inet 10.0.0.1/22 scope global vmbr0\n"))
    assert 0 < live["peak"] <= eng.SWEEP_BATCH and live["now"] == 0
