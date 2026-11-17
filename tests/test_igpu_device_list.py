"""The Intel device list derived from the WhateverXe release that will be installed."""
from __future__ import annotations

import json
import urllib.error

import pytest

from gpu_fakes import FakeRun
from nullmoth_fakes import add_dev
from osx_proxmox_next import igpu_engine as eng

REGS = {"setpci -s 00:02.0 C0.L 50.W": (0, "4b800001\n02c1\n")}


BASE_TEXT = """version|0.8.0
family|adl-p|Alder Lake-P|adl-h
family|rkl|Rocket Lake|tgl
device|46A6|adl-p|tested|Alder Lake-P 46A6
"""


@pytest.fixture(autouse=True)
def _clear_device_environment(monkeypatch):
    monkeypatch.delenv("IGPU_DEVICES_OFFLINE", raising=False)
    monkeypatch.delenv("IGPU_DEVICES", raising=False)


def header(*rows):
    return "\n".join(
        '{ 0x%04X, Family::%s, Display::Whatever, GT::%s, "%s" },' % row
        for row in rows
    )


def release(tag, published_at):
    version = tag.lstrip("vV")
    name = "WhateverXe-%s-RELEASE.zip" % version
    return {
        "tag_name": tag,
        "published_at": published_at,
        "prerelease": False,
        "draft": False,
        "assets": [
            {
                "name": name,
                "digest": "sha256:" + "a" * 64,
                "browser_download_url": (
                    "https://github.com/b00t0x/WhateverXe/releases/download/%s/%s"
                    % (tag, name)
                ),
            }
        ],
    }


class Github:
    def __init__(self, releases):
        self.releases = releases
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return list(self.releases)


def test_parse_header():
    text = header(
        (0x46A6, "AlderLakeP", "Whatever", "Alder Lake-P"),
        (0x46D0, "AlderLakeP", "Unknown", "Alder Lake-P"),
        (0x7D55, "MeteorLake", "Whatever", "Meteor Lake"),
    )

    assert eng.parse_header(text) == [
        [0x46A6, "AlderLakeP", True, "Alder Lake-P"],
        [0x46D0, "AlderLakeP", False, "Alder Lake-P"],
        [0x7D55, "MeteorLake", True, "Meteor Lake"],
    ]


def test_a_model_cannot_add_lines_or_columns_to_the_derived_list():
    hostile = ('{ 0x46A6, Family::AlderLakeP, Display::XeLPD, GT::ADLPMobile, "Iris\ndevice|9999|tgl|tested|x" },\n'
               '{ 0x46A8, Family::AlderLakeP, Display::XeLPD, GT::ADLPMobile, "a|b" },\n'
               '{ 0x46AA, Family::AlderLakeP, Display::XeLPD, GT::ADLPMobile, "fine" },')
    assert eng.parse_header(hostile) == [[0x46AA, "AlderLakeP", True, "fine"]]


def test_derive_text_preserves_tested_and_marks_unavailable():
    entries = [
        [0x46A6, "AlderLakeP", True, "Alder Lake-P"],
        [0x46D0, "AlderLakeP", False, "Alder Lake-P"],
        [0x7D55, "MeteorLake", True, "Meteor Lake"],
    ]
    text = eng.derive_text(entries, "0.9.0", base_text=BASE_TEXT)
    families, devices = eng.parse_devices(text)

    assert devices[0x46A6][1] == "tested"
    assert devices[0x46D0][1] == "nobackend"
    assert devices[0x7D55][1] == "nobackend"
    assert families["new-meteorlake"][0] == "Meteor Lake"
    assert eng.list_version(text) == "0.9.0"


def test_release_tag_latest_and_chosen(tmp_path):
    get = Github(
        [
            release("0.9.0", "2026-11-02T00:00:00Z"),
            release("0.8.0", "2026-11-01T00:00:00Z"),
        ]
    )

    assert eng.release_tag("latest", get=get, cache_dir=tmp_path, now=1000) == "0.9.0"
    assert eng.release_tag("0.8.0", get=get, cache_dir=tmp_path) == "0.8.0"


def test_release_entries_caches_header(tmp_path):
    calls = []

    def header_get(tag):
        calls.append(tag)
        return header((0x46A8, "AlderLakeP", "Whatever", "Alder Lake-P 46A8"))

    expected = [[0x46A8, "AlderLakeP", True, "Alder Lake-P 46A8"]]
    assert eng.release_entries("0.9.0", cache_dir=tmp_path, header_get=header_get) == expected
    assert eng.release_entries("0.9.0", cache_dir=tmp_path, header_get=header_get) == expected
    assert calls == ["0.9.0"]


def test_device_list_uses_new_release_and_caches_header(tmp_path):
    get = Github(
        [
            release("0.9.0", "2026-11-02T00:00:00Z"),
            release("0.8.0", "2026-11-01T00:00:00Z"),
        ]
    )
    calls = []

    def header_get(tag):
        calls.append(tag)
        return header((0x46A8, "AlderLakeP", "Whatever", "Alder Lake-P 46A8"))

    devices = eng.device_list(
        "latest", get=get, header_get=header_get, cache_dir=tmp_path
    )

    assert devices["version"] == "0.9.0"
    assert devices["tag"] == "0.9.0"
    assert devices["source"] != "fallback"
    assert eng.basis(devices) == "Checked against WhateverXe 0.9.0."
    assert eng.lookup(0x46A8, devices["text"])["status"] == "untested"

    kind, _, detail = eng.verdict(0x46A8, devices=devices)
    assert kind == "intel-untested"
    assert "Checked against WhateverXe 0.9.0." in detail

    assert eng.lookup(0x46A6, devices["text"]) is None
    assert eng.verdict(0x46A6, devices=devices)[0] == "unsupported"

    again = eng.device_list(
        "latest", get=get, header_get=header_get, cache_dir=tmp_path
    )
    assert again["text"] == devices["text"]
    assert calls == ["0.9.0"]


def two_releases():
    return Github([release("0.9.0", "2026-11-02T00:00:00Z"), release("0.8.0", "2026-11-01T00:00:00Z")])


def test_device_list_chosen_tag(tmp_path):
    seen = []

    def header_get(tag):
        seen.append(tag)
        return header((0x46A6, "AlderLakeP", "ADLPMobile", "Alder Lake-P"))

    devices = eng.device_list("0.8.0", get=two_releases(), header_get=header_get, cache_dir=tmp_path)
    assert (devices["version"], devices["tag"], seen) == ("0.8.0", "0.8.0", ["0.8.0"])
    assert eng.verdict(0x46A6, devices=devices)[0] == "intel"
    assert eng.release_tag("v0.9.0", get=two_releases(), cache_dir=tmp_path) == "0.9.0"
    assert eng.release_tag("0.1.0", get=two_releases(), cache_dir=tmp_path) == "0.1.0"  # unlisted: the tag is tried as given


def test_a_chip_only_in_the_newer_release_is_offered_with_its_version(tmp_path):
    newer = header((0x46A6, "AlderLakeP", "ADLPMobile", "Iris"), (0x7D55, "MeteorLake", "MTL", "Arc"))
    older = header((0x46A6, "AlderLakeP", "ADLPMobile", "Iris"))
    heads = {"0.9.0": newer, "0.8.0": older}
    latest = eng.device_list("latest", get=two_releases(), header_get=heads.get, cache_dir=tmp_path)
    pinned = eng.device_list("0.8.0", get=two_releases(), header_get=heads.get, cache_dir=tmp_path)
    # a family this tool has no option ROM for is catalogued but refused, and it says which release said so
    kind, _head, detail = eng.verdict(0x7D55, devices=latest)
    assert kind == "unsupported" and "Meteor Lake is listed by WhateverXe" in detail and "WhateverXe 0.9.0" in detail
    assert eng.lookup(0x7D55, pinned["text"]) is None
    assert "Checked against WhateverXe 0.8.0." in eng.verdict(0x7D55, devices=pinned)[2]


def test_a_known_family_chip_added_by_a_newer_release_is_untested_and_supported(tmp_path):
    newer = header((0x46A6, "AlderLakeP", "ADLPMobile", "Iris"), (0x46FF, "AlderLakeP", "ADLPMobile", "New chip"))
    devices = eng.device_list("latest", get=two_releases(), header_get=lambda tag: newer, cache_dir=tmp_path)
    kind, _head, detail = eng.verdict(0x46FF, devices=devices)
    assert kind == "intel-untested" and "Checked against WhateverXe 0.9.0." in detail
    assert eng.lookup(0x46FF, devices["text"])["rom_file"] == "ADL-H_RPL-H_GOPv21_igd.rom"


def test_a_chip_dropped_by_a_release_is_no_longer_offered(tmp_path):
    newer = header((0x9A49, "TigerLake", "TGLGT2", "Iris Xe"))
    devices = eng.device_list("latest", get=two_releases(), header_get=lambda tag: newer, cache_dir=tmp_path)
    kind, _head, detail = eng.verdict(0x46A6, devices=devices)
    assert kind == "unsupported" and "Checked against WhateverXe 0.9.0." in detail
    assert eng.verdict(0x46A6)[0] == "intel"  # the bundled list alone would still have offered it


def test_a_backendless_entry_is_refused_and_a_returned_backend_is_offered(tmp_path):
    none = header((0x4C8A, "RocketLake", "Unknown", "UHD 750"))
    some = header((0x4C8A, "RocketLake", "TGLGT2", "UHD 750"))
    a = eng.device_list("0.8.0", get=two_releases(), header_get=lambda tag: none, cache_dir=tmp_path / "a")
    b = eng.device_list("0.8.0", get=two_releases(), header_get=lambda tag: some, cache_dir=tmp_path / "b")
    assert eng.verdict(0x4C8A, devices=a)[0] == "unsupported"
    assert eng.verdict(0x4C8A, devices=b)[0] == "intel-untested"


def test_offline_falls_back_to_the_bundled_list_and_says_so(tmp_path):
    def down(url):
        raise eng.ReleaseError("Could not reach GitHub")

    def no_page():
        raise eng.ReleaseError("no page either")

    devices = eng.device_list("latest", get=down, cache_dir=tmp_path, redirect=no_page)
    assert devices["source"] == "fallback" and devices["version"] == "0.8.0"
    kind, _head, detail = eng.verdict(0x46A6, devices=devices)
    assert kind == "intel" and "Based on WhateverXe 0.8.0, could not check the newest release." in detail
    chosen = eng.device_list("0.7.0", get=down, header_get=lambda tag: "// moved", cache_dir=tmp_path)
    assert "could not check release 0.7.0" in eng.basis(chosen)
    assert "could not check the newest release" in eng.verdict(0x46A6)[2]  # no list handed in: the bundled one, labelled


def test_unreadable_or_unknown_headers_fall_back(tmp_path):
    get = two_releases()
    for text in ("// moved", ""):
        d = eng.device_list("latest", get=get, header_get=lambda tag: text, cache_dir=tmp_path)
        assert d["source"] == "fallback"
    broken = urllib.error.URLError("boom")

    def raising(tag):
        return eng.fetch_header(tag, opener=lambda *a, **k: (_ for _ in ()).throw(broken))

    assert eng.device_list("latest", get=get, header_get=raising, cache_dir=tmp_path)["source"] == "fallback"
    assert eng.device_list("not a tag!", get=get, cache_dir=tmp_path)["source"] == "fallback"


def test_fetch_header_reads_the_tag_url():
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"// ok"

    seen = []

    def opener(req, timeout):
        seen.append(req.full_url)
        return Resp()

    assert eng.fetch_header("0.9.0", opener=opener) == "// ok"
    assert seen == ["https://raw.githubusercontent.com/b00t0x/WhateverXe/0.9.0/WhateverXe/kern_xelp_devices.hpp"]


def test_latest_tag_is_remembered_for_ten_minutes(tmp_path):
    get = two_releases()
    assert eng.release_tag("latest", get=get, cache_dir=tmp_path, now=1000) == "0.9.0"
    assert eng.release_tag("latest", get=get, cache_dir=tmp_path, now=1100) == "0.9.0"
    assert len(get.urls) == 1
    eng.release_tag("latest", get=get, cache_dir=tmp_path, now=1000 + eng.LATEST_TTL + 1)
    assert len(get.urls) == 2


def test_latest_needs_a_published_release(tmp_path):
    pre = release("0.9.0", "2026-11-02T00:00:00Z")
    pre["prerelease"] = True
    def no_page():
        raise eng.ReleaseError("no page either")

    with pytest.raises(eng.ReleaseError):
        eng.release_tag("latest", get=Github([pre]), cache_dir=tmp_path, redirect=no_page)


def test_a_rate_limited_api_falls_back_to_the_release_page(tmp_path):
    def limited(url):
        raise eng.ReleaseError("GitHub refused the request (rate limit reached).")

    heads = {"0.8.2": header((0x46A6, "AlderLakeP", "ADLPMobile", "Iris"))}
    d = eng.device_list("latest", get=limited, header_get=heads.get, cache_dir=tmp_path, redirect=lambda: "0.8.2")
    assert (d["source"], d["version"]) == ("release", "0.8.2")
    d = eng.device_list("0.8.2", get=limited, header_get=heads.get, cache_dir=tmp_path / "b")  # a tag needs no listing
    assert (d["source"], d["version"]) == ("release", "0.8.2")


def test_latest_tag_from_the_release_page_redirect():
    def answering(location):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": location}, None)
        return opener

    assert eng.latest_tag_from_redirect(answering("https://github.com/b00t0x/WhateverXe/releases/tag/0.8.2")) == "0.8.2"
    for bad in ("https://github.com/b00t0x/WhateverXe/releases", "https://github.com/x/releases/tag/a b", ""):
        with pytest.raises(eng.ReleaseError):
            eng.latest_tag_from_redirect(answering(bad))

    def broken(req, timeout):
        raise urllib.error.URLError("down")

    with pytest.raises(eng.ReleaseError):
        eng.latest_tag_from_redirect(broken)

    def no_redirect(req, timeout):
        return object()  # answered 200: nothing to read a tag from

    with pytest.raises(eng.ReleaseError):
        eng.latest_tag_from_redirect(no_redirect)
    assert eng._NoRedirect().redirect_request(None, None, 302, "", {}, "x") is None


def test_a_corrupt_cache_is_fetched_again_and_a_read_only_cache_is_survived(tmp_path):
    (tmp_path / "0.9.0.json").write_text("{not json")
    calls = []

    def header_get(tag):
        calls.append(tag)
        return header((0x46A8, "AlderLakeP", "ADLPMobile", "x"))

    assert eng.release_entries("0.9.0", cache_dir=tmp_path, header_get=header_get)[0][0] == 0x46A8
    assert json.loads((tmp_path / "0.9.0.json").read_text())[0][0] == 0x46A8
    blocked = tmp_path / "file"
    blocked.write_text("")
    assert eng.release_entries("0.9.1", cache_dir=blocked / "sub", header_get=header_get)  # cannot write: still works
    with pytest.raises(eng.ReleaseError):
        eng.release_entries("bad tag", cache_dir=tmp_path)


def test_igpu_devices_file_overrides_everything(tmp_path, monkeypatch):
    f = tmp_path / "d.txt"
    f.write_text("version|9.9.9\nfamily|tgl|Tiger Lake|tgl\ndevice|9A49|tgl|untested|X\n")
    monkeypatch.setenv("IGPU_DEVICES", str(f))
    d = eng.device_list("latest")
    assert d["source"] == "override" and eng.basis(d) == "Checked against WhateverXe 9.9.9."
    f.write_text("family|tgl|Tiger Lake|tgl\n")
    assert eng.basis(eng.device_list()) == "Checked against a custom device list."


def test_parse_devices_skips_unknown_lines():
    families, devices = eng.parse_devices("version|1\nbogus|x\ndevice|46A6|adl-p\nfamily|a|b|c\n")
    assert families == {"a": ("b", "c")} and devices == {}
    assert eng.list_version("device|1") == "unknown"


def test_the_bundled_list_equals_the_release_derived_one():
    h = (eng.Path(eng.__file__).parent / "data" / "gpu" / "intel_devices.txt").read_text()
    entries = [[int(p[1], 16), {v: k for k, v in eng.UPSTREAM_FAMILIES.items()}[p[2]], p[3] != "nobackend", p[4]]
               for p in (ln.split("|") for ln in h.splitlines() if ln.startswith("device|"))]
    derived = eng.derive_text(entries, "0.8.0")
    assert sorted(ln for ln in derived.splitlines() if ln.startswith("device")) == \
        sorted(ln for ln in h.splitlines() if ln.startswith("device"))


# ── check, the command line and the host adapters ────────────────────

def igd(root, device=0x46A6):
    add_dev(root / "sys", eng.IGD_BDF, 0x8086, device, cls=0x030000, driver="vfio-pci", group=1, boot_vga=True)


def test_check_names_the_release_and_refuses_a_chip_the_release_dropped(tmp_path):
    igd(tmp_path, 0x9A49)
    only = header((0x46A6, "AlderLakeP", "ADLPMobile", "Iris"))
    got = eng.device_list("latest", get=two_releases(), header_get=lambda tag: only, cache_dir=tmp_path / "c")
    rep = eng.check(str(tmp_path), FakeRun(REGS), devices=got)
    assert not rep["ok"] and "Checked against WhateverXe 0.9.0." in " ".join(rep["issues"])
    assert rep["devices"]["version"] == "0.9.0" and rep["verdict"][0] == "unsupported"


def test_check_warns_with_the_release_for_an_untested_chip(tmp_path):
    igd(tmp_path, 0x9A49)
    rep = eng.check(str(tmp_path), FakeRun(REGS))
    assert rep["ok"] and all("Based on WhateverXe 0.8.0, could not check the newest release." in w for w in rep["warnings"])
    assert len(rep["warnings"]) == 2 and rep["devices"]["source"] == "fallback"


def test_check_warns_about_the_fallback_even_for_the_tested_chip(tmp_path):
    igd(tmp_path)
    rep = eng.check(str(tmp_path), FakeRun(REGS))
    assert rep["ok"] and rep["warnings"] == ["The supported-chip list could not be checked against GitHub. "
                                             "Based on WhateverXe 0.8.0, could not check the newest release."]


def test_a_hand_edited_cache_never_raises(tmp_path):
    (tmp_path / "latest.json").write_text(json.dumps({"tag": "0.9.0", "at": "x"}))
    (tmp_path / "0.9.0.json").write_text(json.dumps([["46A6", "AlderLakeP", True, "x"]]))
    (tmp_path / "0.8.0.json").write_text(json.dumps([[0x46A6, "Alder Lake", True, "evil|row"]]))
    assert eng.release_tag("latest", get=two_releases(), cache_dir=tmp_path, now=5) == "0.9.0"
    got = eng.release_entries("0.9.0", cache_dir=tmp_path, header_get=lambda t: header((0x46A8, "AlderLakeP", "ADLPMobile", "ok")))
    assert got == [[0x46A8, "AlderLakeP", True, "ok"]]
    assert eng.release_entries("0.8.0", cache_dir=tmp_path, header_get=lambda t: header((0x46AA, "AlderLakeP", "ADLPMobile", "ok")))[0][0] == 0x46AA
    with pytest.raises(eng.ReleaseError):
        eng.release_entries("0.9.0\n", cache_dir=tmp_path)


def test_cli_verdict_lookup_devices_and_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GPU_ROOT", str(tmp_path))
    monkeypatch.setenv("IGPU_DEVICES_OFFLINE", "1")  # the command line asks GitHub itself: keep it off the network
    igd(tmp_path)
    assert eng.main(["x", "verdict", "46a6", "latest"]) == 0
    assert json.loads(capsys.readouterr().out)["detail"].endswith("could not check the newest release.")
    assert eng.main(["x", "lookup", "46a6"]) == 0
    assert json.loads(capsys.readouterr().out)["model"] == "Intel Iris Xe Graphics"
    assert eng.main(["x", "devices", "0.8.0"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == "fallback" and out["basis"].startswith("Based on WhateverXe 0.8.0")
    monkeypatch.setattr(eng, "default_run", FakeRun(REGS))
    monkeypatch.setattr(eng, "check", lambda root, wex="latest": {"wex": wex})
    assert eng.main(["x", "check", "0.8.0"]) == 0
    assert json.loads(capsys.readouterr().out) == {"wex": "0.8.0"}
