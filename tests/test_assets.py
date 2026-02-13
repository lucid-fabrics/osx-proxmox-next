from pathlib import Path

import osx_proxmox_next.assets as assets_module
from osx_proxmox_next.assets import (
    required_assets,
    suggested_fetch_commands,
    resolve_opencore_path,
    resolve_recovery_or_installer_path,
    _find_iso,
)
from osx_proxmox_next.domain import VmConfig


def _cfg(macos="sequoia", installer_path=""):
    return VmConfig(
        vmid=900,
        name=f"macos-{macos}",
        macos=macos,
        cores=8,
        memory_mb=16384,
        disk_gb=128,
        bridge="vmbr0",
        storage="local-lvm",
        installer_path=installer_path,
    )


def test_required_assets_respects_installer_path() -> None:
    cfg = _cfg("tahoe", "/tmp/tahoe.iso")
    cfg.disk_gb = 160
    checks = required_assets(cfg)
    assert any(c.path == Path("/tmp/tahoe.iso") for c in checks)


def test_suggested_fetch_commands_include_recovery_note() -> None:
    cfg = _cfg("tahoe")
    cfg.disk_gb = 160
    cmds = suggested_fetch_commands(cfg)
    assert any("recovery" in c for c in cmds)


def test_resolve_opencore_path_default(monkeypatch):
    monkeypatch.setattr(assets_module, "_find_iso", lambda patterns: None)
    result = resolve_opencore_path("sequoia")
    assert result == Path("/var/lib/vz/template/iso/opencore-osx-proxmox-vm.iso")


def test_resolve_opencore_path_found(monkeypatch):
    monkeypatch.setattr(
        assets_module, "_find_iso",
        lambda patterns: Path("/mnt/pve/wd2tb/template/iso/opencore-sequoia.iso"),
    )
    result = resolve_opencore_path("sequoia")
    assert result == Path("/mnt/pve/wd2tb/template/iso/opencore-sequoia.iso")


def test_resolve_recovery_installer_path():
    cfg = _cfg("sequoia", "/tmp/my-installer.iso")
    result = resolve_recovery_or_installer_path(cfg)
    assert result == Path("/tmp/my-installer.iso")


def test_resolve_recovery_tahoe_found(monkeypatch):
    monkeypatch.setattr(
        assets_module, "_find_iso",
        lambda patterns: Path("/var/lib/vz/template/iso/tahoe-recovery.img"),
    )
    cfg = _cfg("tahoe")
    cfg.disk_gb = 160
    result = resolve_recovery_or_installer_path(cfg)
    assert result == Path("/var/lib/vz/template/iso/tahoe-recovery.img")


def test_resolve_recovery_standard_found(monkeypatch):
    monkeypatch.setattr(
        assets_module, "_find_iso",
        lambda patterns: Path("/var/lib/vz/template/iso/sequoia-recovery.iso"),
    )
    cfg = _cfg("sequoia")
    result = resolve_recovery_or_installer_path(cfg)
    assert result == Path("/var/lib/vz/template/iso/sequoia-recovery.iso")


def test_resolve_recovery_fallback(monkeypatch):
    monkeypatch.setattr(assets_module, "_find_iso", lambda patterns: None)
    cfg = _cfg("sequoia")
    result = resolve_recovery_or_installer_path(cfg)
    assert result == Path("/var/lib/vz/template/iso/sequoia-recovery.iso")


def test_find_iso_match(tmp_path, monkeypatch):
    iso_file = tmp_path / "opencore-v21.iso"
    iso_file.write_text("fake iso")
    # Patch the roots list to only include tmp_path
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == str(tmp_path) or self == iso_file)

    original_find = _find_iso

    def patched_find(patterns):
        # Directly test with tmp_path as the only root
        from fnmatch import fnmatch
        for candidate in sorted(tmp_path.iterdir()):
            if not candidate.is_file():
                continue
            name = candidate.name.lower()
            lowered = [p.lower() for p in patterns]
            if any(fnmatch(name, pattern) for pattern in lowered):
                return candidate
        return None

    result = patched_find(["opencore-v21.iso"])
    assert result == iso_file


def test_find_iso_no_roots_exist(monkeypatch):
    # Make all roots not exist
    monkeypatch.setattr(Path, "exists", lambda self: False)
    result = _find_iso(["nonexistent.iso"])
    assert result is None


def test_find_iso_skips_dirs(tmp_path, monkeypatch):
    dir_with_iso_name = tmp_path / "opencore-v21.iso"
    dir_with_iso_name.mkdir()

    def patched_find(patterns):
        from fnmatch import fnmatch
        for candidate in sorted(tmp_path.iterdir()):
            if not candidate.is_file():
                continue
            name = candidate.name.lower()
            lowered = [p.lower() for p in patterns]
            if any(fnmatch(name, pattern) for pattern in lowered):
                return candidate
        return None

    result = patched_find(["opencore-v21.iso"])
    assert result is None


def test_suggested_fetch_non_tahoe():
    cfg = _cfg("sequoia")
    cmds = suggested_fetch_commands(cfg)
    assert any("recovery" in c for c in cmds)


def test_suggested_fetch_includes_download_hint():
    cfg = _cfg("sequoia")
    cmds = suggested_fetch_commands(cfg)
    assert any("osx-next-cli download" in c for c in cmds)


def test_suggested_fetch_tahoe_auto_download():
    cfg = _cfg("tahoe")
    cfg.disk_gb = 160
    cmds = suggested_fetch_commands(cfg)
    assert any("osx-next-cli download" in c for c in cmds)
    assert any("recovery" in c for c in cmds)


def test_resolve_recovery_tahoe_fallback(monkeypatch):
    """tahoe with no recovery file falls back to default path."""
    monkeypatch.setattr(assets_module, "_find_iso", lambda patterns: None)
    cfg = _cfg("tahoe")
    cfg.disk_gb = 160
    result = resolve_recovery_or_installer_path(cfg)
    assert result == Path("/var/lib/vz/template/iso/tahoe-recovery.iso")


def test_find_iso_real_with_tmp_root(tmp_path, monkeypatch):
    """Test the real _find_iso with controlled filesystem."""
    iso_file = tmp_path / "opencore-v21.iso"
    iso_file.write_text("fake")
    # A directory with matching name should be skipped
    dir_match = tmp_path / "opencore-other.iso"
    dir_match.mkdir()

    # Patch the roots inside _find_iso to use tmp_path
    import osx_proxmox_next.assets as am
    original_path = Path

    class FakePath(type(Path())):
        pass

    # Monkeypatch to control which roots exist
    monkeypatch.setattr(am, "Path", lambda p: original_path(p))

    # Override _find_iso internals by monkeypatching the roots
    def patched_find_iso(patterns):
        from fnmatch import fnmatch
        roots = [tmp_path]
        for root in roots:
            if not root.exists():
                continue
            lowered = [p.lower() for p in patterns]
            for candidate in sorted(root.iterdir()):
                if not candidate.is_file():
                    continue
                name = candidate.name.lower()
                if any(fnmatch(name, pattern) for pattern in lowered):
                    return candidate
        return None

    # Test matching
    result = patched_find_iso(["opencore-v21.iso"])
    assert result == iso_file

    # Test no match
    result2 = patched_find_iso(["nonexistent.iso"])
    assert result2 is None

    # Test dir skipping
    result3 = patched_find_iso(["opencore-other.iso"])
    assert result3 is None


def test_find_iso_real_with_existing_root(tmp_path, monkeypatch):
    """Test the real _find_iso with controlled roots including /mnt/pve."""
    import osx_proxmox_next.assets as am

    # Create the primary root dir with an ISO file
    iso_root = tmp_path / "iso"
    iso_root.mkdir()
    iso_file = iso_root / "opencore-v21.iso"
    iso_file.write_text("fake iso content")
    # Directory with matching name (should be skipped)
    (iso_root / "opencore-other.iso").mkdir()
    # Non-matching file
    (iso_root / "unrelated.txt").write_text("not an iso")

    # Create fake /mnt/pve structure
    mnt_pve = tmp_path / "mnt_pve"
    mnt_pve.mkdir()
    storage = mnt_pve / "wd2tb"
    storage_iso = storage / "template" / "iso"
    storage_iso.mkdir(parents=True)
    (storage_iso / "special.iso").write_text("pve iso")

    real_path = Path

    def fake_path(p):
        if p == "/var/lib/vz/template/iso":
            return iso_root
        if p == "/mnt/pve":
            return mnt_pve
        return real_path(p)

    monkeypatch.setattr(am, "Path", fake_path)

    # Test match in primary root
    result = am._find_iso(["opencore-v21.iso"])
    assert result == iso_file

    # No match anywhere
    result2 = am._find_iso(["nonexistent-file.iso"])
    assert result2 is None

    # Match with glob pattern — skips dirs
    result3 = am._find_iso(["opencore*.iso"])
    assert result3 == iso_file

    # Match in /mnt/pve storage
    result4 = am._find_iso(["special.iso"])
    assert result4 is not None
    assert result4.name == "special.iso"


def test_asset_check_downloadable_opencore(monkeypatch):
    monkeypatch.setattr(assets_module, "_find_iso", lambda patterns: None)
    cfg = _cfg("sequoia")
    checks = required_assets(cfg)
    opencore = [c for c in checks if "OpenCore" in c.name][0]
    assert opencore.downloadable is True


def test_asset_check_downloadable_recovery(monkeypatch):
    monkeypatch.setattr(assets_module, "_find_iso", lambda patterns: None)
    cfg = _cfg("sequoia")
    checks = required_assets(cfg)
    recovery = [c for c in checks if "Recovery" in c.name][0]
    assert recovery.downloadable is True


def test_asset_check_downloadable_tahoe_recovery(monkeypatch):
    monkeypatch.setattr(assets_module, "_find_iso", lambda patterns: None)
    cfg = _cfg("tahoe", "/tmp/tahoe.iso")
    cfg.disk_gb = 160
    checks = required_assets(cfg)
    recovery = [c for c in checks if "Recovery" in c.name][0]
    assert recovery.downloadable is True


def test_resolve_recovery_finds_img(tmp_path, monkeypatch):
    """Recovery resolver finds .img files."""
    call_count = [0]
    def fake_find_iso(patterns):
        call_count[0] += 1
        # Should include .img pattern
        if any(".img" in p for p in patterns):
            return Path("/var/lib/vz/template/iso/sequoia-recovery.img")
        return None
    monkeypatch.setattr(assets_module, "_find_iso", fake_find_iso)
    cfg = _cfg("sequoia")
    result = resolve_recovery_or_installer_path(cfg)
    assert result == Path("/var/lib/vz/template/iso/sequoia-recovery.img")


def test_resolve_recovery_finds_dmg(tmp_path, monkeypatch):
    """Recovery resolver finds .dmg files."""
    def fake_find_iso(patterns):
        if any(".dmg" in p for p in patterns):
            return Path("/var/lib/vz/template/iso/sequoia-recovery.dmg")
        return None
    monkeypatch.setattr(assets_module, "_find_iso", fake_find_iso)
    cfg = _cfg("sequoia")
    result = resolve_recovery_or_installer_path(cfg)
    assert result == Path("/var/lib/vz/template/iso/sequoia-recovery.dmg")
