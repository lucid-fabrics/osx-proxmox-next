from pathlib import Path

from osx_proxmox_next.defaults import default_disk_gb, detect_cpu_cores, detect_cpu_vendor, detect_memory_mb


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
