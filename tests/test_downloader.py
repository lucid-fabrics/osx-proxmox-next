from __future__ import annotations

import gzip
import io
import json
import plistlib
import subprocess as real_subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import osx_proxmox_next.downloader as dl_module
from osx_proxmox_next.downloader import (
    DownloadError,
    DownloadProgress,
    download_opencore,
    download_recovery,
    _build_recovery_image,
    _download_file,
    _download_file_with_token,
    _download_tahoe_installer,
    _extract_sharedsupport_dmg,
    _fetch_github_release,
    _find_installer_url,
    _find_release_asset,
    _get_recovery_session,
    _get_recovery_image_info,
    _http_get_bytes,
)


def _make_response(data: bytes, content_length: int | None = None):
    """Create a fake HTTP response object."""
    resp = MagicMock()
    resp.read = MagicMock(side_effect=[data, b""])
    resp.headers = {"Content-Length": str(content_length) if content_length else "0"}
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_chunked_response(chunks: list[bytes], content_length: int | None = None):
    """Create a fake HTTP response that returns data in chunks."""
    resp = MagicMock()
    resp.read = MagicMock(side_effect=chunks + [b""])
    resp.headers = {"Content-Length": str(content_length) if content_length else "0"}
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestDownloadOpencore:
    def test_success(self, tmp_path, monkeypatch):
        release_json = {
            "tag_name": "v0.3.0",
            "assets": [
                {
                    "name": "opencore-sequoia.iso",
                    "browser_download_url": "https://example.com/opencore-sequoia.iso",
                }
            ],
        }
        api_resp = _make_response(json.dumps(release_json).encode())
        file_data = b"fake-iso-content-" * 100
        file_resp = _make_chunked_response([file_data], len(file_data))

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return api_resp
            return file_resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(dl_module, "__version__", "0.3.0")
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        result = download_opencore("sequoia", tmp_path)
        assert result == tmp_path / "opencore-sequoia.iso"
        assert result.exists()

    def test_fallback_latest(self, tmp_path, monkeypatch):
        release_json = {
            "tag_name": "v0.2.0",
            "assets": [
                {
                    "name": "opencore-sequoia.iso",
                    "browser_download_url": "https://example.com/opencore-sequoia.iso",
                }
            ],
        }
        api_resp = _make_response(json.dumps(release_json).encode())
        file_data = b"iso-data"
        file_resp = _make_chunked_response([file_data], len(file_data))

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))
            if call_count[0] == 2:
                return api_resp
            return file_resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(dl_module, "__version__", "99.99.99")
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        result = download_opencore("sequoia", tmp_path)
        assert result == tmp_path / "opencore-sequoia.iso"

    def test_no_matching_asset(self, tmp_path, monkeypatch):
        release_json = {
            "tag_name": "v0.3.0",
            "assets": [
                {"name": "other-file.zip", "browser_download_url": "https://example.com/other.zip"}
            ],
        }
        api_resp = _make_response(json.dumps(release_json).encode())

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: api_resp)
        monkeypatch.setattr(dl_module, "__version__", "0.3.0")

        with pytest.raises(DownloadError, match="not found in release"):
            download_opencore("sequoia", tmp_path)

    def test_existing_file_skips_download(self, tmp_path, monkeypatch):
        existing = tmp_path / "opencore-sequoia.iso"
        existing.write_text("already here")

        result = download_opencore("sequoia", tmp_path)
        assert result == existing


class TestDownloadRecovery:
    def test_tahoe_delegates_to_installer(self, tmp_path, monkeypatch):
        called = [False]

        def fake_tahoe(dest_dir, on_progress=None):
            called[0] = True
            dest = dest_dir / "tahoe-full-installer.img"
            dest.write_bytes(b"fake-installer")
            return dest

        monkeypatch.setattr(dl_module, "_download_tahoe_installer", fake_tahoe)
        result = download_recovery("tahoe", tmp_path)
        assert called[0]
        assert result == tmp_path / "tahoe-full-installer.img"

    def test_unknown_macos_rejected(self, tmp_path):
        with pytest.raises(DownloadError, match="No recovery board ID"):
            download_recovery("unknown_os", tmp_path)

    def test_existing_file_skips_download(self, tmp_path):
        existing = tmp_path / "sequoia-recovery.img"
        existing.write_text("already here")

        result = download_recovery("sequoia", tmp_path)
        assert result == existing

    def test_success(self, tmp_path, monkeypatch):
        session_resp = _make_response(b"")
        session_resp.headers = {"Set-Cookie": "session=ABC123; path=/; HttpOnly"}

        image_info_resp = _make_response(
            b"AP: 041-00000\nAU: https://oscdn.apple.com/BaseSystem.dmg\n"
            b"AH: abc123\nAT: TOKEN123\nCU: https://oscdn.apple.com/BaseSystem.chunklist\n"
            b"CH: def456\nCT: TOKEN456\n"
        )

        dmg_data = b"basesystem-dmg-content"
        dmg_resp = _make_chunked_response([dmg_data], len(dmg_data))
        chunklist_data = b"chunklist-content"
        chunklist_resp = _make_chunked_response([chunklist_data], len(chunklist_data))

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return session_resp
            if call_count[0] == 2:
                return image_info_resp
            if call_count[0] == 3:
                return dmg_resp
            return chunklist_resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        def fake_build_recovery_image(dmg_path, chunklist_path, dest):
            assert dmg_path.exists()
            assert chunklist_path.exists()
            dest.write_bytes(b"built-recovery-image")

        monkeypatch.setattr(dl_module, "_build_recovery_image", fake_build_recovery_image)

        result = download_recovery("sonoma", tmp_path)
        assert result == tmp_path / "sonoma-recovery.img"
        assert result.exists()
        # Intermediate files should be cleaned up
        assert not (tmp_path / "sonoma-BaseSystem.dmg").exists()
        assert not (tmp_path / "sonoma-BaseSystem.chunklist").exists()


class TestDownloadFile:
    def test_partial_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            raise ConnectionError("network failure")

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)

        dest = tmp_path / "test.iso"
        with pytest.raises(DownloadError, match="Download failed after"):
            _download_file("https://example.com/file.iso", dest, None, "opencore")

        assert not dest.exists()
        assert not (tmp_path / "test.iso.part").exists()

    def test_partial_file_cleaned_up(self, tmp_path, monkeypatch):
        """When .part file is created but download fails mid-stream, it gets cleaned up."""
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        original_do_download = dl_module._do_download
        call_count = [0]

        def failing_do_download(url, dest, on_progress, phase, extra_headers=None):
            call_count[0] += 1
            # Write partial data then fail
            dest.write_bytes(b"partial data")
            raise ConnectionError("mid-download failure")

        monkeypatch.setattr(dl_module, "_do_download", failing_do_download)

        dest = tmp_path / "test.iso"
        with pytest.raises(DownloadError, match="Download failed after"):
            _download_file("https://example.com/file.iso", dest, None, "opencore")

        assert not dest.exists()
        assert not (tmp_path / "test.iso.part").exists()

    def test_retry_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        file_data = b"success-data"
        file_resp = _make_chunked_response([file_data], len(file_data))

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("transient error")
            return file_resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)

        dest = tmp_path / "retry-test.iso"
        _download_file("https://example.com/file.iso", dest, None, "opencore")
        assert dest.exists()
        assert dest.read_bytes() == file_data

    def test_progress_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        chunk1 = b"a" * 1000
        chunk2 = b"b" * 500
        total = len(chunk1) + len(chunk2)
        file_resp = _make_chunked_response([chunk1, chunk2], total)

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: file_resp)

        progress_calls: list[DownloadProgress] = []

        def on_progress(p: DownloadProgress) -> None:
            progress_calls.append(p)

        dest = tmp_path / "progress-test.iso"
        _download_file("https://example.com/file.iso", dest, on_progress, "recovery")

        assert len(progress_calls) == 2
        assert progress_calls[0].downloaded == 1000
        assert progress_calls[0].total == total
        assert progress_calls[0].phase == "recovery"
        assert progress_calls[1].downloaded == 1500
        assert progress_calls[1].total == total


class TestFetchGithubRelease:
    def test_tag_success(self, monkeypatch):
        release = {"tag_name": "v0.3.0", "assets": []}
        resp = _make_response(json.dumps(release).encode())
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)
        monkeypatch.setattr(dl_module, "__version__", "0.3.0")

        result = _fetch_github_release("0.3.0")
        assert result["tag_name"] == "v0.3.0"

    def test_both_fail(self, monkeypatch):
        def fail(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fail)

        with pytest.raises(DownloadError, match="Could not fetch GitHub release"):
            _fetch_github_release("99.0.0")


class TestFindReleaseAsset:
    def test_found(self):
        release = {
            "tag_name": "v0.3.0",
            "assets": [
                {"name": "opencore-sequoia.iso", "browser_download_url": "https://dl.example.com/oc.iso"},
            ],
        }
        url = _find_release_asset(release, "opencore-sequoia.iso")
        assert url == "https://dl.example.com/oc.iso"

    def test_not_found(self):
        release = {"tag_name": "v0.3.0", "assets": []}
        with pytest.raises(DownloadError, match="not found in release"):
            _find_release_asset(release, "opencore-sequoia.iso")

    def test_empty_url(self):
        release = {
            "tag_name": "v0.3.0",
            "assets": [
                {"name": "opencore-sequoia.iso", "browser_download_url": ""},
            ],
        }
        with pytest.raises(DownloadError, match="not found in release"):
            _find_release_asset(release, "opencore-sequoia.iso")


class TestHttpGetJson:
    def test_network_error_propagates(self, monkeypatch):
        from osx_proxmox_next.downloader import _http_get_json

        def fail(req, timeout=None):
            raise ConnectionError("no network")

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fail)

        with pytest.raises(ConnectionError, match="no network"):
            _http_get_json("https://api.github.com/repos/test/releases/latest")


class TestGetRecoverySession:
    def test_success(self, monkeypatch):
        resp = _make_response(b"")
        resp.headers = {"Set-Cookie": "session=ABC123; path=/; HttpOnly"}
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        result = _get_recovery_session()
        assert result == "session=ABC123"

    def test_network_error(self, monkeypatch):
        def fail(req, timeout=None):
            raise ConnectionError("no network")

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fail)

        with pytest.raises(DownloadError, match="Failed to get recovery session"):
            _get_recovery_session()

    def test_session_after_other_parts(self, monkeypatch):
        """Session cookie found after non-session parts in Set-Cookie."""
        resp = _make_response(b"")
        resp.headers = {"Set-Cookie": "path=/; HttpOnly; session=XYZ789"}
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        result = _get_recovery_session()
        assert result == "session=XYZ789"

    def test_no_session_in_cookie_parts(self, monkeypatch):
        """Set-Cookie header exists but has no session= part."""
        resp = _make_response(b"")
        resp.headers = {"Set-Cookie": "path=/; HttpOnly; other=value"}
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        with pytest.raises(DownloadError, match="No session cookie"):
            _get_recovery_session()

    def test_no_session_cookie(self, monkeypatch):
        resp = _make_response(b"")
        resp.headers = {"Content-Type": "text/html"}
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        with pytest.raises(DownloadError, match="No session cookie"):
            _get_recovery_session()


class TestGetRecoveryImageInfo:
    def test_success(self, monkeypatch):
        resp = _make_response(
            b"AP: 041-00000\nAU: https://oscdn.apple.com/BaseSystem.dmg\n"
            b"AH: abc123\nAT: TOKEN123\nCU: https://oscdn.apple.com/chunklist\n"
            b"CH: def456\nCT: TOKEN456\n"
        )
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        result = _get_recovery_image_info("session=ABC", "Mac-827FAC58A8FDFA22")
        assert result["AU"] == "https://oscdn.apple.com/BaseSystem.dmg"
        assert result["AT"] == "TOKEN123"

    def test_network_error(self, monkeypatch):
        def fail(req, timeout=None):
            raise ConnectionError("no network")

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fail)

        with pytest.raises(DownloadError, match="Failed to get recovery image info"):
            _get_recovery_image_info("session=ABC", "Mac-TEST")

    def test_missing_required_key_at(self, monkeypatch):
        resp = _make_response(b"AP: 041-00000\nAU: https://example.com/img\n")
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        with pytest.raises(DownloadError, match="Missing key 'AT'"):
            _get_recovery_image_info("session=ABC", "Mac-TEST")

    def test_missing_required_key_cu(self, monkeypatch):
        resp = _make_response(b"AU: https://example.com/img\nAT: TOKEN\n")
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        with pytest.raises(DownloadError, match="Missing key 'CU'"):
            _get_recovery_image_info("session=ABC", "Mac-TEST")

    def test_ignores_lines_without_separator(self, monkeypatch):
        resp = _make_response(
            b"no-separator-line\nAU: https://example.com/img\nAT: TOKEN\n"
            b"CU: https://example.com/chunklist\nCT: CTOKEN\n"
        )
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        result = _get_recovery_image_info("session=ABC", "Mac-TEST")
        assert result["AU"] == "https://example.com/img"
        assert result["AT"] == "TOKEN"


class TestDownloadFileWithToken:
    def test_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        file_data = b"recovery-data"
        file_resp = _make_chunked_response([file_data], len(file_data))
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: file_resp)

        dest = tmp_path / "recovery.img"
        _download_file_with_token("https://oscdn.apple.com/BaseSystem.dmg", "TOKEN", dest, None, "recovery")
        assert dest.exists()
        assert dest.read_bytes() == file_data

    def test_retry_and_fail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        def fail(req, timeout=None):
            raise ConnectionError("network failure")

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fail)

        dest = tmp_path / "recovery.img"
        with pytest.raises(DownloadError, match="Download failed after"):
            _download_file_with_token("https://oscdn.apple.com/img", "TOKEN", dest, None, "recovery")
        assert not dest.exists()

    def test_partial_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        def failing_do_download(url, dest, on_progress, phase, extra_headers=None):
            dest.write_bytes(b"partial data")
            raise ConnectionError("mid-download failure")

        monkeypatch.setattr(dl_module, "_do_download", failing_do_download)

        dest = tmp_path / "recovery.img"
        with pytest.raises(DownloadError, match="Download failed after"):
            _download_file_with_token("https://oscdn.apple.com/img", "TOKEN", dest, None, "recovery")
        assert not dest.exists()
        assert not (tmp_path / "recovery.img.part").exists()

    def test_progress_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        chunk1 = b"a" * 1000
        chunk2 = b"b" * 500
        total = len(chunk1) + len(chunk2)
        file_resp = _make_chunked_response([chunk1, chunk2], total)

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: file_resp)

        progress_calls: list[DownloadProgress] = []

        def on_progress(p: DownloadProgress) -> None:
            progress_calls.append(p)

        dest = tmp_path / "recovery.img"
        _download_file_with_token("https://oscdn.apple.com/img", "TOKEN", dest, on_progress, "recovery")
        assert len(progress_calls) == 2
        assert progress_calls[0].phase == "recovery"


class TestBuildRecoveryImage:
    def test_success(self, tmp_path, monkeypatch):
        import subprocess as real_subprocess
        from osx_proxmox_next.downloader import _build_recovery_image

        dmg = tmp_path / "BaseSystem.dmg"
        chunklist = tmp_path / "BaseSystem.chunklist"
        dmg.write_bytes(b"x" * 1024)
        chunklist.write_bytes(b"y" * 64)
        dest = tmp_path / "recovery.img"

        def fake_run(argv, **kw):
            assert argv[0] == "dmg2img"
            assert argv[1] == str(dmg)
            assert argv[2] == str(dest)
            dest.write_bytes(b"\x00" * 2048)
            return real_subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        _build_recovery_image(dmg, chunklist, dest)
        assert dest.exists()

    def test_failure_cleans_up(self, tmp_path, monkeypatch):
        import subprocess as real_subprocess
        from osx_proxmox_next.downloader import _build_recovery_image

        dmg = tmp_path / "BaseSystem.dmg"
        chunklist = tmp_path / "BaseSystem.chunklist"
        dmg.write_bytes(b"x" * 1024)
        chunklist.write_bytes(b"y" * 64)
        dest = tmp_path / "recovery.img"
        dest.write_bytes(b"partial")

        def fake_run(argv, **kw):
            raise real_subprocess.CalledProcessError(1, argv, stderr=b"dmg2img failed")

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        with pytest.raises(DownloadError, match="Failed to convert recovery DMG"):
            _build_recovery_image(dmg, chunklist, dest)
        assert not dest.exists()

    def test_failure_no_dest_to_clean(self, tmp_path, monkeypatch):
        import subprocess as real_subprocess
        from osx_proxmox_next.downloader import _build_recovery_image

        dmg = tmp_path / "BaseSystem.dmg"
        chunklist = tmp_path / "BaseSystem.chunklist"
        dmg.write_bytes(b"x" * 1024)
        chunklist.write_bytes(b"y" * 64)
        dest = tmp_path / "recovery.img"

        def fake_run(argv, **kw):
            raise real_subprocess.CalledProcessError(1, argv, stderr=b"dmg2img failed")

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        with pytest.raises(DownloadError, match="Failed to convert recovery DMG"):
            _build_recovery_image(dmg, chunklist, dest)
        assert not dest.exists()

    def test_failure_dmg2img_not_found(self, tmp_path, monkeypatch):
        from osx_proxmox_next.downloader import _build_recovery_image

        dmg = tmp_path / "BaseSystem.dmg"
        chunklist = tmp_path / "BaseSystem.chunklist"
        dmg.write_bytes(b"x" * 1024)
        chunklist.write_bytes(b"y" * 64)
        dest = tmp_path / "recovery.img"

        def fake_run(argv, **kw):
            raise FileNotFoundError("dmg2img")

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        with pytest.raises(DownloadError, match="dmg2img is required but not installed"):
            _build_recovery_image(dmg, chunklist, dest)


def _make_sucatalog(products: list[dict]) -> bytes:
    """Build a gzipped plist sucatalog from a list of product dicts."""
    catalog = {"Products": {}}
    for i, prod in enumerate(products):
        catalog["Products"][f"0{i+1:02d}-{i+1:05d}"] = prod
    raw = plistlib.dumps(catalog)
    return gzip.compress(raw)


def _make_dist_xml(title: str) -> bytes:
    return f'<?xml version="1.0"?><installer><title>{title}</title></installer>'.encode()


class TestFindInstallerUrl:
    def _catalog_with_tahoe(self) -> bytes:
        return _make_sucatalog([
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/content/foo/InstallAssistant.pkg", "Size": 13_000_000_000},
                ],
                "Distributions": {"English": "https://swdist.apple.com/content/foo/English.dist"},
                "PostDate": "2025-06-10T00:00:00Z",
            }
        ])

    def test_success(self, monkeypatch):
        catalog_gz = self._catalog_with_tahoe()
        dist_xml = _make_dist_xml("macOS Tahoe")

        call_count = [0]

        def fake_http_get_bytes(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return catalog_gz
            return dist_xml

        monkeypatch.setattr(dl_module, "_http_get_bytes", fake_http_get_bytes)

        result = _find_installer_url("tahoe")
        assert "InstallAssistant.pkg" in result

    def test_no_match_title(self, monkeypatch):
        catalog_gz = self._catalog_with_tahoe()
        dist_xml = _make_dist_xml("macOS Sequoia")

        call_count = [0]

        def fake_http_get_bytes(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return catalog_gz
            return dist_xml

        monkeypatch.setattr(dl_module, "_http_get_bytes", fake_http_get_bytes)

        with pytest.raises(DownloadError, match="No installer found"):
            _find_installer_url("tahoe")

    def test_empty_catalog(self, monkeypatch):
        catalog_gz = _make_sucatalog([])
        monkeypatch.setattr(dl_module, "_http_get_bytes", lambda url: catalog_gz)

        with pytest.raises(DownloadError, match="No installer found"):
            _find_installer_url("tahoe")

    def test_unknown_macos(self):
        with pytest.raises(DownloadError, match="No installer title mapping"):
            _find_installer_url("catalina")

    def test_skips_small_packages(self, monkeypatch):
        catalog_gz = _make_sucatalog([
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/content/foo/InstallAssistant.pkg", "Size": 100_000},
                ],
                "Distributions": {"English": "https://swdist.apple.com/content/foo/English.dist"},
                "PostDate": "2025-06-10T00:00:00Z",
            }
        ])
        monkeypatch.setattr(dl_module, "_http_get_bytes", lambda url: catalog_gz)

        with pytest.raises(DownloadError, match="No installer found"):
            _find_installer_url("tahoe")

    def test_picks_most_recent(self, monkeypatch):
        catalog_gz = _make_sucatalog([
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/old/InstallAssistant.pkg", "Size": 13_000_000_000},
                ],
                "Distributions": {"English": "https://swdist.apple.com/old/English.dist"},
                "PostDate": "2025-06-01T00:00:00Z",
            },
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/new/InstallAssistant.pkg", "Size": 13_000_000_000},
                ],
                "Distributions": {"English": "https://swdist.apple.com/new/English.dist"},
                "PostDate": "2025-06-10T00:00:00Z",
            },
        ])
        dist_xml = _make_dist_xml("macOS Tahoe")

        def fake_http_get_bytes(url):
            if url.endswith(".sucatalog.gz"):
                return catalog_gz
            return dist_xml

        monkeypatch.setattr(dl_module, "_http_get_bytes", fake_http_get_bytes)

        result = _find_installer_url("tahoe")
        assert "new" in result

    def test_skips_product_without_distributions(self, monkeypatch):
        catalog_gz = _make_sucatalog([
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/content/foo/InstallAssistant.pkg", "Size": 13_000_000_000},
                ],
                "Distributions": {},
                "PostDate": "2025-06-10T00:00:00Z",
            }
        ])
        monkeypatch.setattr(dl_module, "_http_get_bytes", lambda url: catalog_gz)

        with pytest.raises(DownloadError, match="No installer found"):
            _find_installer_url("tahoe")

    def test_skips_product_with_dist_fetch_error(self, monkeypatch):
        catalog_gz = _make_sucatalog([
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/content/foo/InstallAssistant.pkg", "Size": 13_000_000_000},
                ],
                "Distributions": {"English": "https://swdist.apple.com/content/foo/English.dist"},
                "PostDate": "2025-06-10T00:00:00Z",
            }
        ])

        def fake_http_get_bytes(url):
            if url.endswith(".sucatalog.gz"):
                return catalog_gz
            raise ConnectionError("dist fetch failed")

        monkeypatch.setattr(dl_module, "_http_get_bytes", fake_http_get_bytes)

        with pytest.raises(DownloadError, match="No installer found"):
            _find_installer_url("tahoe")

    def test_skips_product_with_no_title_in_dist(self, monkeypatch):
        catalog_gz = _make_sucatalog([
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/content/foo/InstallAssistant.pkg", "Size": 13_000_000_000},
                ],
                "Distributions": {"English": "https://swdist.apple.com/content/foo/English.dist"},
                "PostDate": "2025-06-10T00:00:00Z",
            }
        ])

        def fake_http_get_bytes(url):
            if url.endswith(".sucatalog.gz"):
                return catalog_gz
            return b'<?xml version="1.0"?><installer><description>No title here</description></installer>'

        monkeypatch.setattr(dl_module, "_http_get_bytes", fake_http_get_bytes)

        with pytest.raises(DownloadError, match="No installer found"):
            _find_installer_url("tahoe")

    def test_uses_en_fallback_distribution(self, monkeypatch):
        catalog_gz = _make_sucatalog([
            {
                "Packages": [
                    {"URL": "https://swcdn.apple.com/content/foo/InstallAssistant.pkg", "Size": 13_000_000_000},
                ],
                "Distributions": {"en": "https://swdist.apple.com/content/foo/en.dist"},
                "PostDate": "2025-06-10T00:00:00Z",
            }
        ])
        dist_xml = _make_dist_xml("macOS Tahoe")

        def fake_http_get_bytes(url):
            if url.endswith(".sucatalog.gz"):
                return catalog_gz
            return dist_xml

        monkeypatch.setattr(dl_module, "_http_get_bytes", fake_http_get_bytes)

        result = _find_installer_url("tahoe")
        assert "InstallAssistant.pkg" in result


class TestHttpGetBytes:
    def test_success(self, monkeypatch):
        resp = _make_response(b"hello bytes")
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)
        result = _http_get_bytes("https://example.com/data")
        assert result == b"hello bytes"

    def test_network_error(self, monkeypatch):
        def fail(req, timeout=None):
            raise ConnectionError("no network")
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fail)

        with pytest.raises(DownloadError, match="Failed to fetch"):
            _http_get_bytes("https://example.com/data")


class TestExtractSharedsupportDmg:
    def test_success(self, tmp_path, monkeypatch):
        pkg_path = tmp_path / "test.pkg"
        pkg_path.write_bytes(b"fake pkg")

        def fake_run(argv, **kw):
            extract_dir = Path(argv[3][2:])  # -o<dir>
            inner = extract_dir / "SharedSupport.dmg"
            inner.parent.mkdir(parents=True, exist_ok=True)
            inner.write_bytes(b"fake dmg content")
            return real_subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        result = _extract_sharedsupport_dmg(pkg_path, tmp_path)
        assert result == tmp_path / "tahoe-SharedSupport.dmg"
        assert result.exists()
        assert result.read_bytes() == b"fake dmg content"
        # Extract dir should be cleaned up
        assert not (tmp_path / "tahoe-pkg-extract").exists()

    def test_7z_not_found(self, tmp_path, monkeypatch):
        pkg_path = tmp_path / "test.pkg"
        pkg_path.write_bytes(b"fake pkg")

        def fake_run(argv, **kw):
            raise FileNotFoundError("7z")

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        with pytest.raises(DownloadError, match="7z is required but not installed"):
            _extract_sharedsupport_dmg(pkg_path, tmp_path)

    def test_7z_extract_fails(self, tmp_path, monkeypatch):
        pkg_path = tmp_path / "test.pkg"
        pkg_path.write_bytes(b"fake pkg")

        def fake_run(argv, **kw):
            raise real_subprocess.CalledProcessError(1, argv, stderr=b"extraction error")

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        with pytest.raises(DownloadError, match="Failed to extract installer"):
            _extract_sharedsupport_dmg(pkg_path, tmp_path)

    def test_7z_extract_fails_cleans_extract_dir(self, tmp_path, monkeypatch):
        pkg_path = tmp_path / "test.pkg"
        pkg_path.write_bytes(b"fake pkg")
        extract_dir = tmp_path / "tahoe-pkg-extract"
        extract_dir.mkdir()
        (extract_dir / "partial.bin").write_bytes(b"partial")

        def fake_run(argv, **kw):
            raise real_subprocess.CalledProcessError(1, argv, stderr=b"extraction error")

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        with pytest.raises(DownloadError, match="Failed to extract installer"):
            _extract_sharedsupport_dmg(pkg_path, tmp_path)
        assert not extract_dir.exists()

    def test_no_dmg_in_pkg(self, tmp_path, monkeypatch):
        pkg_path = tmp_path / "test.pkg"
        pkg_path.write_bytes(b"fake pkg")

        def fake_run(argv, **kw):
            extract_dir = Path(argv[3][2:])
            extract_dir.mkdir(parents=True, exist_ok=True)
            (extract_dir / "SomeOtherFile.bin").write_bytes(b"not a dmg")
            return real_subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(dl_module.subprocess, "run", fake_run)

        with pytest.raises(DownloadError, match="SharedSupport.dmg not found"):
            _extract_sharedsupport_dmg(pkg_path, tmp_path)


class TestDownloadTahoeInstaller:
    def test_existing_skip(self, tmp_path):
        dest = tmp_path / "tahoe-full-installer.img"
        dest.write_bytes(b"existing installer")
        result = _download_tahoe_installer(tmp_path)
        assert result == dest

    def test_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        monkeypatch.setattr(
            dl_module, "_find_installer_url",
            lambda macos: "https://swcdn.apple.com/content/foo/InstallAssistant.pkg",
        )

        pkg_data = b"fake-pkg-data"
        file_resp = _make_chunked_response([pkg_data], len(pkg_data))
        monkeypatch.setattr(
            dl_module.urllib.request, "urlopen",
            lambda req, timeout=None: file_resp,
        )

        def fake_extract(pkg_path, dest_dir):
            dmg = dest_dir / "tahoe-SharedSupport.dmg"
            dmg.write_bytes(b"fake-dmg")
            return dmg

        monkeypatch.setattr(dl_module, "_extract_sharedsupport_dmg", fake_extract)

        def fake_build(dmg_path, _chunklist, dest):
            dest.write_bytes(b"final-image")

        monkeypatch.setattr(dl_module, "_build_recovery_image", fake_build)

        result = _download_tahoe_installer(tmp_path)
        assert result == tmp_path / "tahoe-full-installer.img"
        assert result.exists()
        # Intermediate files should be cleaned up
        assert not (tmp_path / "tahoe-InstallAssistant.pkg").exists()
        assert not (tmp_path / "tahoe-SharedSupport.dmg").exists()

    def test_catalog_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            dl_module, "_find_installer_url",
            MagicMock(side_effect=DownloadError("No installer found")),
        )

        with pytest.raises(DownloadError, match="No installer found"):
            _download_tahoe_installer(tmp_path)

    def test_progress_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        monkeypatch.setattr(
            dl_module, "_find_installer_url",
            lambda macos: "https://swcdn.apple.com/content/foo/InstallAssistant.pkg",
        )

        chunk1 = b"a" * 1000
        chunk2 = b"b" * 500
        total = len(chunk1) + len(chunk2)
        file_resp = _make_chunked_response([chunk1, chunk2], total)
        monkeypatch.setattr(
            dl_module.urllib.request, "urlopen",
            lambda req, timeout=None: file_resp,
        )

        def fake_extract(pkg_path, dest_dir):
            dmg = dest_dir / "tahoe-SharedSupport.dmg"
            dmg.write_bytes(b"fake-dmg")
            return dmg

        monkeypatch.setattr(dl_module, "_extract_sharedsupport_dmg", fake_extract)

        def fake_build(dmg_path, _chunklist, dest):
            dest.write_bytes(b"final-image")

        monkeypatch.setattr(dl_module, "_build_recovery_image", fake_build)

        progress_calls: list[DownloadProgress] = []

        def on_progress(p: DownloadProgress) -> None:
            progress_calls.append(p)

        result = _download_tahoe_installer(tmp_path, on_progress)
        assert result.exists()
        assert len(progress_calls) == 2
        assert progress_calls[0].phase == "installer"
        assert progress_calls[1].phase == "installer"
