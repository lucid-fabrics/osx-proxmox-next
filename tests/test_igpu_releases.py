"""WhateverXe and WhateverXe Graphics come from GitHub releases: latest by default, any published tag on request,
every file checked against the asset digest, no silent fallback."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from osx_proxmox_next import igpu_engine as eng
from osx_proxmox_next.igpu_engine import github_get as real_github_get  # bound before the autouse fake replaces it

WEX = "b00t0x/WhateverXe"
GFX = "b00t0x/WhateverXe-Graphics"
SHA = "a" * 64


def release(repo, tag, name, sha=SHA, pre=False, draft=False, when="2026-11-01T00:00:00Z", url=None):
    asset = {"name": name, "browser_download_url": url or f"https://github.com/{repo}/releases/download/{tag}/{name}"}
    if sha:
        asset["digest"] = "sha256:" + sha
    return {"tag_name": tag, "prerelease": pre, "draft": draft, "published_at": when,
            "assets": [{"name": "SHA256SUMS", "digest": "sha256:" + "0" * 64,
                        "browser_download_url": "https://github.com/x/SHA256SUMS"}, asset]}


class Github:
    """A fake `get`: list answers per repo, tag answers per (repo, tag), everything else raises like GitHub does."""

    def __init__(self, lists=None, tags=None):
        self.lists, self.tags, self.urls = lists or {}, tags or {}, []

    def __call__(self, url):
        self.urls.append(url)
        repo = url.split("/repos/")[1].split("/releases")[0]
        if "/releases/tags/" in url:
            tag = url.rsplit("/", 1)[1]
            if (repo, tag) not in self.tags:
                raise eng.ReleaseError("GitHub has no such release.")
            return self.tags[(repo, tag)]
        return self.lists[repo]


WEX_LIST = [release(WEX, "0.9.1", "WhateverXe-0.9.1-RELEASE.zip", pre=True, when="2026-11-05T00:00:00Z"),
            release(WEX, "0.9.0", "WhateverXe-0.9.0-RELEASE.zip", sha="9" * 64, when="2026-11-01T00:00:00Z"),
            release(WEX, "0.8.0", "WhateverXe-0.8.0-RELEASE.zip", sha="8" * 64, when="2026-10-01T00:00:00Z"),
            release(WEX, "0.7.9", "WhateverXe-0.7.9-RELEASE.zip", draft=True, when="2026-12-01T00:00:00Z")]
GFX_LIST = [release(GFX, "v0.4.1", "WhateverXe-Graphics-0.4.1.pkg", sha="4" * 64),
            release(GFX, "v0.4.0", "WhateverXe-Graphics-0.4.0.pkg", sha="3" * 64, when="2026-10-01T00:00:00Z")]


@pytest.fixture
def gh():
    return Github({WEX: WEX_LIST, GFX: GFX_LIST})


# ── choosing a release ───────────────────────────────────────────────

def test_latest_is_the_newest_release_that_is_not_a_prerelease(gh):
    pick = eng.resolve("whateverxe", "latest", gh)
    assert pick["tag"] == "0.9.0" and pick["version"] == "0.9.0" and pick["source"] == "latest"
    assert pick["sha256"] == "9" * 64 and pick["name"] == "WhateverXe-0.9.0-RELEASE.zip"
    assert eng.resolve("whateverxe", "", gh)["tag"] == "0.9.0"  # empty means latest


def test_the_graphics_package_strips_the_v_from_its_version_and_ignores_other_assets(gh):
    pick = eng.resolve("graphics", "latest", gh)
    assert pick["tag"] == "v0.4.1" and pick["version"] == "0.4.1" and pick["name"] == "WhateverXe-Graphics-0.4.1.pkg"


def test_any_published_tag_can_be_chosen_by_tag_or_bare_version(gh):
    assert eng.resolve("whateverxe", "0.8.0", gh)["source"] == "chosen"
    assert eng.resolve("graphics", "v0.4.0", gh)["sha256"] == "3" * 64
    assert eng.resolve("graphics", "0.4.0", gh)["tag"] == "v0.4.0"
    assert eng.resolve("whateverxe", "0.9.1", gh)["prerelease"] is True  # a prerelease only on request


def test_the_list_is_newest_first_without_drafts_or_releases_that_lack_the_file(gh):
    gh.lists[WEX] = [*WEX_LIST, {"tag_name": "0.1", "assets": []}, release(WEX, "0.2", "Other.zip")]
    tags = [p["tag"] for p in eng.list_releases("whateverxe", gh)]
    assert tags == ["0.9.1", "0.9.0", "0.8.0"]


def test_an_older_tag_beyond_the_first_page_is_asked_for_directly(gh):
    gh.tags[(WEX, "0.1.0")] = release(WEX, "0.1.0", "WhateverXe-0.1.0-RELEASE.zip")
    assert eng.resolve("whateverxe", "0.1.0", gh)["tag"] == "0.1.0"
    assert any(u.endswith("/releases/tags/0.1.0") for u in gh.urls)


def test_an_unknown_tag_is_an_error_that_points_at_the_list(gh):
    with pytest.raises(eng.ReleaseError, match="no release tagged 9.9.9.*igpu-versions"):
        eng.resolve("whateverxe", "9.9.9", gh)


def test_a_tag_whose_release_has_no_usable_file_is_refused(gh):
    gh.tags[(WEX, "0.0.1")] = {"tag_name": "0.0.1", "assets": []}
    with pytest.raises(eng.ReleaseError, match="has no download this tool can use"):
        eng.resolve("whateverxe", "0.0.1", gh)


@pytest.mark.parametrize("bad", ["../x", "a b", "x" * 80, "-v", "0.9;rm"])
def test_a_tag_that_is_not_a_tag_is_refused_before_any_request(gh, bad):
    with pytest.raises(eng.ReleaseError, match="is not a release tag"):
        eng.resolve("whateverxe", bad, gh)
    assert gh.urls == []


def test_no_stable_release_at_all_is_an_error(gh):
    gh.lists[WEX] = [WEX_LIST[0]]
    with pytest.raises(eng.ReleaseError, match="No published release of WhateverXe"):
        eng.resolve("whateverxe", "latest", gh)


# ── verification ─────────────────────────────────────────────────────

def test_a_release_without_a_digest_is_a_hard_failure_for_that_asset(gh):
    gh.lists[WEX] = [release(WEX, "1.0.0", "WhateverXe-1.0.0-RELEASE.zip", sha=None)]
    with pytest.raises(eng.ReleaseError, match="no sha256 digest.*cannot be verified"):
        eng.resolve("whateverxe", "latest", gh)  # it does not skip to an older release behind the user's back
    with pytest.raises(eng.ReleaseError, match="no sha256 digest"):
        eng.resolve("whateverxe", "1.0.0", gh)


def test_a_digest_that_is_not_a_sha256_counts_as_missing(gh):
    bad = release(WEX, "1.0.0", "WhateverXe-1.0.0-RELEASE.zip")
    bad["assets"][1]["digest"] = "md5:abc"
    gh.lists[WEX] = [bad]
    with pytest.raises(eng.ReleaseError, match="no sha256 digest"):
        eng.resolve("whateverxe", "latest", gh)


def test_a_download_address_outside_the_projects_releases_is_refused(gh):
    gh.lists[WEX] = [release(WEX, "1.0.0", "WhateverXe-1.0.0-RELEASE.zip", url="https://evil.example/x.zip")]
    with pytest.raises(eng.ReleaseError, match="unexpected download address"):
        eng.resolve("whateverxe", "latest", gh)
    gh.lists[WEX] = [release(WEX, "1.0.0", "WhateverXe-1.0.0-RELEASE.zip",
                             url="https://github.com/someone-else/WhateverXe/releases/download/1.0.0/a.zip")]
    with pytest.raises(eng.ReleaseError, match="unexpected download address"):
        eng.resolve("whateverxe", "latest", gh)


def test_the_download_is_checked_against_the_digest_and_a_mismatch_is_an_error(tmp_path, monkeypatch):
    pick = {"url": "https://github.com/b00t0x/WhateverXe/releases/download/0.9.0/W.zip", "sha256": SHA, "name": "W.zip"}
    monkeypatch.setattr(eng.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"not the file"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        eng.fetch_release(pick, tmp_path)
    assert not list(tmp_path.iterdir())  # nothing is kept
    good = {**pick, "sha256": eng.hashlib.sha256(b"not the file").hexdigest()}
    assert eng.fetch_release(good, tmp_path) == tmp_path / "W.zip"
    monkeypatch.setattr(eng.urllib.request, "urlopen", lambda *a, **k: pytest.fail("a cached file is not downloaded again"))
    assert eng.fetch_release(good, tmp_path) == tmp_path / "W.zip"


def test_select_assets_returns_both_and_names_the_one_that_fails(gh):
    picks = eng.select_assets("0.8.0", "latest", gh)
    assert picks["whateverxe"]["tag"] == "0.8.0" and picks["graphics"]["tag"] == "v0.4.1"
    with pytest.raises(eng.ReleaseError, match="WhateverXe Graphics"):
        eng.select_assets("latest", "7.7.7", gh)


# ── GitHub itself ────────────────────────────────────────────────────

class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_github_get_sends_the_token_when_there_is_one(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen.update(req.headers)
        return Resp(b'[{"tag_name": "1"}]')
    monkeypatch.setattr(eng.urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert real_github_get("https://api.github.com/x") == [{"tag_name": "1"}]
    assert "Authorization" not in seen
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    real_github_get("https://api.github.com/x")
    assert seen["Authorization"] == "Bearer tok" and seen["User-agent"] == "osx-proxmox-next"


@pytest.mark.parametrize("code,text", [(403, "rate limit"), (429, "rate limit"), (404, "no such release"),
                                       (500, "HTTP 500")])
def test_github_get_turns_http_errors_into_advice(monkeypatch, code, text):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
    monkeypatch.setattr(eng.urllib.request, "urlopen", urlopen)
    with pytest.raises(eng.ReleaseError, match="(?i)" + text) as exc:
        real_github_get("https://api.github.com/x")
    if code in (403, 429):
        assert "GITHUB_TOKEN" in str(exc.value) and "again" in str(exc.value)


@pytest.mark.parametrize("failure", [urllib.error.URLError("dns"), TimeoutError("slow"), ValueError("bad json")])
def test_github_get_says_to_check_the_network_and_retry_when_it_cannot_reach_github(monkeypatch, failure):
    def urlopen(req, timeout):
        raise failure
    monkeypatch.setattr(eng.urllib.request, "urlopen", urlopen)
    with pytest.raises(eng.ReleaseError, match="Could not reach GitHub.*run the command again"):
        real_github_get("https://api.github.com/x")


def test_there_is_no_hidden_fallback_to_an_old_version():
    assert set(eng.PINS) == {"amfipass"}
    source = Path(eng.__file__).read_text()
    assert "0.8.0" not in source and "0.4.1" not in source


# ── what is installed, per VM ────────────────────────────────────────

def test_state_is_written_merged_read_and_described(tmp_path):
    picks = {"whateverxe": {"version": "0.9.0", "tag": "0.9.0", "sha256": SHA, "source": "latest"},
             "graphics": {"version": "0.4.0", "tag": "v0.4.0", "sha256": SHA, "source": "chosen"}}
    eng.write_state(5, eng.picks_state(picks, "tahoe"), base=tmp_path)
    eng.write_state(5, {"package_installed": True}, base=tmp_path)
    state = eng.read_state(5, base=tmp_path)
    assert state["whateverxe_version"] == "0.9.0" and state["package_installed"] is True and state["macos"] == "tahoe"
    assert state["amfipass_version"] == eng.AMFIPASS_VERSION and state["updated"].endswith("Z")
    assert eng.describe_state(state) == ["WhateverXe 0.9.0 (latest when installed)",
                                         "WhateverXe Graphics 0.4.0 (chosen by the user)",
                                         "AMFIPass 1.4.1-Mod (pinned)"]
    assert eng.describe_state({}) == []


def test_reading_a_missing_or_broken_state_file_gives_nothing(tmp_path):
    assert eng.read_state(5, base=tmp_path) == {}
    (tmp_path / "5.json").write_text("{not json")
    assert eng.read_state(5, base=tmp_path) == {}
    (tmp_path / "5.json").write_text("[1]")
    assert eng.read_state(5, base=tmp_path) == {}


# ── the command line the bash installer uses ─────────────────────────

def test_cli_assets_releases_and_state_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IGPU_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(eng, "fetch", lambda url, sha, dest: Path(dest))
    assert eng.main(["x", "assets", str(tmp_path), "0.8.0"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["whateverxe"]["version"] == "0.8.0" and out["whateverxe"]["path"].endswith("WhateverXe-0.8.0-RELEASE.zip")
    assert out["graphics"]["source"] == "latest"
    monkeypatch.setattr(eng.sys, "stdin", io.StringIO(json.dumps({k: out[k] for k in ("whateverxe", "graphics")})))
    assert eng.main(["x", "state-record", "5", "sequoia"]) == 0
    monkeypatch.setattr(eng.sys, "stdin", io.StringIO('{"package_installed": true}'))
    assert eng.main(["x", "state-write", "5"]) == 0
    capsys.readouterr()
    assert eng.main(["x", "state-show", "5"]) == 0
    shown = capsys.readouterr().out
    assert "WhateverXe 0.8.0 (chosen by the user)" in shown and "AMFIPass" in shown
    assert eng.main(["x", "releases", "graphics"]) == 0
    assert [r["tag"] for r in json.loads(capsys.readouterr().out)] == ["v0.4.1"]


def test_cli_reports_release_errors_on_stderr_with_exit_1(tmp_path, monkeypatch, capsys):
    def boom(url):
        raise eng.ReleaseError("Could not reach GitHub (x). Check the network and run the command again.")
    monkeypatch.setattr(eng, "github_get", boom)
    assert eng.main(["x", "assets", str(tmp_path)]) == 1
    assert eng.main(["x", "releases", "whateverxe"]) == 1
    assert capsys.readouterr().err.count("ERROR: Could not reach GitHub") == 2


# ── names and tags are plain words ───────────────────────────────────

def test_an_asset_name_with_a_path_in_it_is_never_used(gh):
    gh.lists[WEX] = [release(WEX, "1.0.0", "WhateverXe-../../etc/x-RELEASE.zip")]
    assert eng.list_releases("whateverxe", gh) == []


@pytest.mark.parametrize("tag", ['0.9"+x', "-evil", "a b", "x" * 80, ""])
def test_a_release_tag_that_is_not_a_plain_word_is_dropped_from_the_list(gh, tag):
    gh.lists[WEX] = [release(WEX, tag, "WhateverXe-1-RELEASE.zip"), *WEX_LIST]
    assert [p["tag"] for p in eng.list_releases("whateverxe", gh)] == ["0.9.1", "0.9.0", "0.8.0"]
