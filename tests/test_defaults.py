from pathlib import Path

from osx_proxmox_next.defaults import (
    DEFAULT_ISO_DIR,
    _resolve_iso_path,
    default_disk_gb,
    detect_cpu_cores,
    detect_cpu_vendor,
    detect_iso_storage,
    detect_memory_mb,
)


def test_detect_defaults_return_sane_values() -> None:
    assert detect_cpu_cores() >= 2
    assert detect_memory_mb() >= 4096


def test_default_disk_gb_by_target() -> None:
    assert default_disk_gb("tahoe") >= default_disk_gb("sequoia")
    assert default_disk_gb("sonoma") >= 64


def test_detect_cpu_cores_high(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 32)
    assert detect_cpu_cores() == 16


def test_detect_cpu_cores_low(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 4)
    assert detect_cpu_cores() == 4


def test_detect_cpu_cores_none(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert detect_cpu_cores() == 4


def test_detect_memory_from_meminfo(monkeypatch, tmp_path):
    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text("MemTotal:       32768000 kB\nMemFree:        100 kB\n")
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_meminfo if p == "/proc/meminfo" else Path(p))
    result = detect_memory_mb()
    assert result == 16000


def test_detect_memory_no_meminfo(monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: Path("/nonexistent/meminfo"))
    assert detect_memory_mb() == 8192


def test_default_disk_sonoma():
    assert default_disk_gb("sonoma") == 96


def test_default_disk_ventura():
    assert default_disk_gb("ventura") == 80


def test_detect_memory_meminfo_no_memtotal(monkeypatch, tmp_path):
    """meminfo exists but no MemTotal line → fallback."""
    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text("MemFree:        100 kB\nBuffers:        200 kB\n")
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_meminfo if p == "/proc/meminfo" else Path(p))
    assert detect_memory_mb() == 8192


def test_detect_memory_meminfo_bad_format(monkeypatch, tmp_path):
    """MemTotal line exists but value isn't digit."""
    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text("MemTotal:       abc kB\n")
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_meminfo if p == "/proc/meminfo" else Path(p))
    assert detect_memory_mb() == 8192


def test_detect_memory_meminfo_short_parts(monkeypatch, tmp_path):
    """MemTotal line exists but has only one part."""
    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text("MemTotal:\n")
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_meminfo if p == "/proc/meminfo" else Path(p))
    assert detect_memory_mb() == 8192


def test_detect_cpu_vendor_amd(monkeypatch, tmp_path):
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text("vendor_id\t: AuthenticAMD\nmodel name\t: AMD Ryzen 9\n")
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    assert detect_cpu_vendor() == "AMD"


def test_detect_cpu_vendor_intel(monkeypatch, tmp_path):
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text("vendor_id\t: GenuineIntel\nmodel name\t: Intel Xeon\n")
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    assert detect_cpu_vendor() == "Intel"


def test_detect_cpu_vendor_missing(monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: Path("/nonexistent/cpuinfo"))
    assert detect_cpu_vendor() == "Intel"


def test_detect_cpu_vendor_no_vendor_line(monkeypatch, tmp_path):
    """cpuinfo exists but has no vendor_id line → fallback to Intel."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text("model name\t: Some CPU\nflags\t: sse sse2\n")
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    assert detect_cpu_vendor() == "Intel"


def test_detect_iso_storage_pvesm_fails(monkeypatch):
    """When pvesm is unavailable, fall back to DEFAULT_ISO_DIR."""
    import subprocess
    monkeypatch.setattr(
        subprocess, "check_output",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no pvesm")),
    )
    dirs = detect_iso_storage()
    assert DEFAULT_ISO_DIR in dirs


def test_detect_iso_storage_parses_pvesm(monkeypatch):
    """Parses pvesm status output and resolves paths; skips inactive storage."""
    import subprocess
    pvesm_output = (
        "Name         Type     Status           Total            Used       Available        %\n"
        "local          dir     active       100000000        50000000        50000000   50.00%\n"
        "nas-iso        nfs     active       200000000       100000000       100000000   50.00%\n"
        "offline-nas    nfs     inactive     300000000       150000000       150000000   50.00%\n"
    )
    resolve_calls = []

    def fake_check_output(cmd, **kw):
        if "status" in cmd:
            return pvesm_output
        raise Exception("nope")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    # Patch _resolve_iso_path to track which storage IDs are resolved
    import osx_proxmox_next.defaults as dm
    original_resolve = dm._resolve_iso_path
    def tracking_resolve(sid):
        resolve_calls.append(sid)
        return original_resolve(sid)
    monkeypatch.setattr(dm, "_resolve_iso_path", tracking_resolve)

    dirs = detect_iso_storage()
    assert DEFAULT_ISO_DIR in dirs
    # inactive storage should NOT be resolved
    assert "offline-nas" not in resolve_calls


def test_resolve_iso_path_local():
    """local storage resolves to default ISO dir."""
    assert _resolve_iso_path("local") == DEFAULT_ISO_DIR


def test_resolve_iso_path_unknown(monkeypatch):
    """Unknown storage with no pvesm and no /mnt/pve path returns None."""
    import subprocess
    monkeypatch.setattr(
        subprocess, "check_output",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        "osx_proxmox_next.defaults.Path",
        lambda p: Path("/nonexistent") if "/mnt/pve/" in str(p) else Path(p),
    )
    assert _resolve_iso_path("nonexistent-storage") is None


def test_resolve_iso_path_mnt_pve(tmp_path, monkeypatch):
    """Resolves storage via /mnt/pve/{id}/template/iso if it exists."""
    import subprocess
    iso_dir = tmp_path / "template" / "iso"
    iso_dir.mkdir(parents=True)
    monkeypatch.setattr(
        subprocess, "check_output",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        "osx_proxmox_next.defaults.Path",
        lambda p: iso_dir if "/mnt/pve/" in str(p) else Path(p),
    )
    result = _resolve_iso_path("my-nas")
    assert result == str(iso_dir)


def test_resolve_iso_path_pvesm_success(monkeypatch):
    """pvesm path returns a file path; we extract the parent directory."""
    import subprocess
    monkeypatch.setattr(
        subprocess, "check_output",
        lambda *a, **kw: "/mnt/pve/nas/template/iso/probe.iso\n",
    )
    result = _resolve_iso_path("nas")
    assert result == "/mnt/pve/nas/template/iso"


def test_detect_iso_storage_resolves_path(monkeypatch):
    """detect_iso_storage resolves storage IDs via _resolve_iso_path."""
    import subprocess
    pvesm_output = (
        "Name         Type     Status           Total            Used       Available        %\n"
        "nas-iso        nfs     active       200000000       100000000       100000000   50.00%\n"
    )

    def fake_check_output(cmd, **kw):
        if "status" in cmd:
            return pvesm_output
        # pvesm path call
        return "/mnt/pve/nas-iso/template/iso/probe.iso\n"

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    dirs = detect_iso_storage()
    assert "/mnt/pve/nas-iso/template/iso" in dirs
    assert DEFAULT_ISO_DIR in dirs
