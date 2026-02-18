from __future__ import annotations

import io
import json
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
    _fetch_github_release,
    _find_release_asset,
    _get_recovery_session,
    _get_recovery_image_info,
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

        with pytest.raises(DownloadError, match="No OpenCore asset found"):
            download_opencore("sequoia", tmp_path)

    def test_fallback_to_universal(self, tmp_path, monkeypatch):
        """When version-specific ISO missing, falls back to universal OC image."""
        release_json = {
            "tag_name": "v0.3.0",
            "assets": [
                {
                    "name": "opencore-osx-proxmox-vm.iso",
                    "browser_download_url": "https://example.com/opencore-osx-proxmox-vm.iso",
                }
            ],
        }
        api_resp = _make_response(json.dumps(release_json).encode())
        file_data = b"universal-oc"
        file_resp = _make_chunked_response([file_data], len(file_data))

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return api_resp
            return file_resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(dl_module, "__version__", "0.3.0")

        result = download_opencore("tahoe", tmp_path)
        assert result == tmp_path / "opencore-osx-proxmox-vm.iso"
        assert result.exists()

    def test_existing_universal_skips_download(self, tmp_path, monkeypatch):
        """If universal OC image exists locally, skip download even for tahoe."""
        existing = tmp_path / "opencore-osx-proxmox-vm.iso"
        existing.write_text("already here")

        result = download_opencore("tahoe", tmp_path)
        assert result == existing

    def test_existing_file_skips_download(self, tmp_path, monkeypatch):
        existing = tmp_path / "opencore-sequoia.iso"
        existing.write_text("already here")

        result = download_opencore("sequoia", tmp_path)
        assert result == existing


class TestDownloadRecovery:
    def test_tahoe_uses_osrecovery_with_latest(self, tmp_path, monkeypatch):
        """Tahoe uses the same osrecovery path as Sonoma/Sequoia but with os=latest."""
        session_resp = _make_response(b"")
        session_resp.headers = {"Set-Cookie": "session=ABC123; path=/; HttpOnly"}

        captured_os_type = [None]
        original_get_info = dl_module._get_recovery_image_info

        def spy_get_info(session, board_id, os_type="default"):
            captured_os_type[0] = os_type
            return {
                "AU": "https://oscdn.apple.com/BaseSystem.dmg",
                "AT": "TOKEN123",
                "CU": "https://oscdn.apple.com/BaseSystem.chunklist",
                "CT": "TOKEN456",
            }

        monkeypatch.setattr(dl_module, "_get_recovery_image_info", spy_get_info)
        monkeypatch.setattr(dl_module, "_get_recovery_session", lambda: "session=ABC")

        dmg_data = b"basesystem-dmg-content"
        dmg_resp = _make_chunked_response([dmg_data], len(dmg_data))
        chunklist_data = b"chunklist-content"
        chunklist_resp = _make_chunked_response([chunklist_data], len(chunklist_data))

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return dmg_resp
            return chunklist_resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        def fake_build(dmg_path, chunklist_path, dest):
            dest.write_bytes(b"built-recovery-image")

        monkeypatch.setattr(dl_module, "_build_recovery_image", fake_build)

        result = download_recovery("tahoe", tmp_path)
        assert result == tmp_path / "tahoe-recovery.img"
        assert result.exists()
        assert captured_os_type[0] == "latest"

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

    def test_not_found_required(self):
        release = {"tag_name": "v0.3.0", "assets": []}
        with pytest.raises(DownloadError, match="not found in release"):
            _find_release_asset(release, "opencore-sequoia.iso", required=True)

    def test_not_found_optional(self):
        release = {"tag_name": "v0.3.0", "assets": []}
        result = _find_release_asset(release, "opencore-sequoia.iso", required=False)
        assert result == ""

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

    def test_os_type_passed_in_body(self, monkeypatch):
        """Verify that os_type parameter is included in POST body."""
        captured_body = []

        def capturing_urlopen(req, timeout=None):
            captured_body.append(req.data.decode("utf-8"))
            resp = MagicMock()
            resp.read.return_value = (
                b"AU: https://x.com/img\nAT: T\nCU: https://x.com/cl\nCT: T\n"
            )
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(dl_module.urllib.request, "urlopen", capturing_urlopen)

        _get_recovery_image_info("session=ABC", "Mac-TEST", os_type="latest")
        assert "os=latest" in captured_body[0]

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


class TestRecoveryOsType:
    def test_sonoma_uses_default(self, tmp_path, monkeypatch):
        """Sonoma uses os=default for osrecovery."""
        captured = []

        def spy_get_info(session, board_id, os_type="default"):
            captured.append(os_type)
            return {
                "AU": "https://oscdn.apple.com/img",
                "AT": "T", "CU": "https://oscdn.apple.com/cl", "CT": "T",
            }

        monkeypatch.setattr(dl_module, "_get_recovery_image_info", spy_get_info)
        monkeypatch.setattr(dl_module, "_get_recovery_session", lambda: "session=X")
        monkeypatch.setattr(dl_module, "_download_file_with_token",
                            lambda *a, **kw: None)
        monkeypatch.setattr(dl_module, "_build_recovery_image",
                            lambda dmg, cl, dest: dest.write_bytes(b"img"))
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        download_recovery("sonoma", tmp_path)
        assert captured[0] == "default"

    def test_ventura_uses_default(self, tmp_path, monkeypatch):
        """Ventura uses os=default for osrecovery."""
        captured = []

        def spy_get_info(session, board_id, os_type="default"):
            captured.append(os_type)
            return {
                "AU": "https://oscdn.apple.com/img",
                "AT": "T", "CU": "https://oscdn.apple.com/cl", "CT": "T",
            }

        monkeypatch.setattr(dl_module, "_get_recovery_image_info", spy_get_info)
        monkeypatch.setattr(dl_module, "_get_recovery_session", lambda: "session=X")
        monkeypatch.setattr(dl_module, "_download_file_with_token",
                            lambda *a, **kw: None)
        monkeypatch.setattr(dl_module, "_build_recovery_image",
                            lambda dmg, cl, dest: dest.write_bytes(b"img"))
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        download_recovery("ventura", tmp_path)
        assert captured[0] == "default"

    def test_tahoe_uses_latest(self, tmp_path, monkeypatch):
        """Tahoe uses os=latest for osrecovery to get macOS 26."""
        captured = []

        def spy_get_info(session, board_id, os_type="default"):
            captured.append(os_type)
            return {
                "AU": "https://oscdn.apple.com/img",
                "AT": "T", "CU": "https://oscdn.apple.com/cl", "CT": "T",
            }

        monkeypatch.setattr(dl_module, "_get_recovery_image_info", spy_get_info)
        monkeypatch.setattr(dl_module, "_get_recovery_session", lambda: "session=X")
        monkeypatch.setattr(dl_module, "_download_file_with_token",
                            lambda *a, **kw: None)
        monkeypatch.setattr(dl_module, "_build_recovery_image",
                            lambda dmg, cl, dest: dest.write_bytes(b"img"))
        monkeypatch.setattr(dl_module.time, "sleep", lambda s: None)

        download_recovery("tahoe", tmp_path)
        assert captured[0] == "latest"
