from __future__ import annotations

import io
import json
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
    _download_file,
    _fetch_github_release,
    _find_release_asset,
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
    def test_tahoe_rejected(self, tmp_path):
        with pytest.raises(DownloadError, match="Tahoe requires a full installer"):
            download_recovery("tahoe", tmp_path)

    def test_unknown_macos_rejected(self, tmp_path):
        with pytest.raises(DownloadError, match="No recovery catalog entry"):
            download_recovery("unknown_os", tmp_path)

    def test_existing_file_skips_download(self, tmp_path):
        existing = tmp_path / "sequoia-recovery.img"
        existing.write_text("already here")

        result = download_recovery("sequoia", tmp_path)
        assert result == existing

    def test_success(self, tmp_path, monkeypatch):
        import gzip
        import plistlib

        catalog = {
            "Products": {
                "prod1": {
                    "Packages": [
                        {"URL": "https://apple.com/BaseSystem.dmg", "Size": 500},
                    ],
                    "ExtendedMetaInfo": {
                        "InstallAssistantPackageIdentifiers": {
                            "SharedSupport": "com.apple.pkg.InstallAssistant.macOS15"
                        }
                    },
                }
            }
        }
        catalog_xml = plistlib.dumps(catalog)
        catalog_gz = gzip.compress(catalog_xml)
        catalog_resp = _make_response(catalog_gz)

        file_data = b"basesystem-dmg-content"
        file_resp = _make_chunked_response([file_data], len(file_data))

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return catalog_resp
            return file_resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        result = download_recovery("sequoia", tmp_path)
        assert result == tmp_path / "sequoia-recovery.img"
        assert result.exists()


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

        def failing_do_download(url, dest, on_progress, phase):
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


class TestFindBaseSystemUrl:
    def test_catalog_fetch_error(self, monkeypatch):
        from osx_proxmox_next.downloader import _find_base_system_url

        def fail(req, timeout=None):
            raise ConnectionError("no network")

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fail)

        with pytest.raises(DownloadError, match="Failed to fetch Apple catalog"):
            _find_base_system_url("https://example.com/catalog.gz", "Mac-ABC")

    def test_bad_plist(self, monkeypatch):
        from osx_proxmox_next.downloader import _find_base_system_url

        resp = _make_response(b"not valid plist data")
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        with pytest.raises(DownloadError, match="Failed to parse Apple catalog"):
            _find_base_system_url("https://example.com/catalog.gz", "Mac-ABC")

    def test_no_basesystem_in_catalog(self, monkeypatch):
        import gzip
        import plistlib
        from osx_proxmox_next.downloader import _find_base_system_url

        catalog = {
            "Products": {
                "prod1": {
                    "Packages": [
                        {"URL": "https://apple.com/other.pkg"},
                    ],
                }
            }
        }
        catalog_xml = plistlib.dumps(catalog)
        catalog_gz = gzip.compress(catalog_xml)
        resp = _make_response(catalog_gz)
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        with pytest.raises(DownloadError, match="Could not find BaseSystem.dmg"):
            _find_base_system_url("https://example.com/catalog.gz", "Mac-ZZZZZ")

    def test_board_id_match(self, monkeypatch):
        import gzip
        import plistlib
        from osx_proxmox_next.downloader import _find_base_system_url

        catalog = {
            "Products": {
                "prod1": {
                    "Packages": [
                        {"URL": "https://apple.com/BaseSystem.dmg"},
                    ],
                    "ExtendedMetaInfo": {
                        "InstallAssistantPackageIdentifiers": {
                            "SharedSupport": "Mac-27AD2F918AE68F61"
                        }
                    },
                },
                "prod2": {
                    "Packages": [
                        {"URL": "https://apple.com/other-BaseSystem.dmg"},
                    ],
                    "ExtendedMetaInfo": {},
                },
            }
        }
        catalog_xml = plistlib.dumps(catalog)
        catalog_gz = gzip.compress(catalog_xml)
        resp = _make_response(catalog_gz)
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        result = _find_base_system_url("https://example.com/catalog.gz", "Mac-27AD2F918AE68F61")
        assert result == "https://apple.com/BaseSystem.dmg"

    def test_uncompressed_catalog(self, monkeypatch):
        import plistlib
        from osx_proxmox_next.downloader import _find_base_system_url

        catalog = {
            "Products": {
                "prod1": {
                    "Packages": [
                        {"URL": "https://apple.com/BaseSystem.dmg"},
                    ],
                    "ExtendedMetaInfo": {
                        "InstallAssistantPackageIdentifiers": {
                            "SharedSupport": "Mac-TESTBOARD"
                        }
                    },
                }
            }
        }
        catalog_xml = plistlib.dumps(catalog)
        # Send uncompressed data (not gzipped)
        resp = _make_response(catalog_xml)
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        result = _find_base_system_url("https://example.com/catalog.xml", "Mac-TESTBOARD")
        assert "BaseSystem.dmg" in result

    def test_fallback_candidate_when_no_board_match(self, monkeypatch):
        """When board_id doesn't match any product, falls back to first BaseSystem.dmg found."""
        import gzip
        import plistlib
        from osx_proxmox_next.downloader import _find_base_system_url

        catalog = {
            "Products": {
                "prod1": {
                    "Packages": [
                        {"URL": "https://apple.com/fallback-BaseSystem.dmg"},
                    ],
                    "ExtendedMetaInfo": {
                        "InstallAssistantPackageIdentifiers": {
                            "SharedSupport": "Mac-DIFFERENT"
                        }
                    },
                }
            }
        }
        catalog_xml = plistlib.dumps(catalog)
        catalog_gz = gzip.compress(catalog_xml)
        resp = _make_response(catalog_gz)
        monkeypatch.setattr(dl_module.urllib.request, "urlopen", lambda req, timeout=None: resp)

        result = _find_base_system_url("https://example.com/catalog.gz", "Mac-NOMATCH")
        assert result == "https://apple.com/fallback-BaseSystem.dmg"
