from pathlib import Path

from osx_proxmox_next.defaults import (
    DEFAULT_ISO_DIR,
    CpuInfo,
    _resolve_iso_path,
    default_disk_gb,
    detect_cpu_cores,
    detect_cpu_info,
    detect_cpu_vendor,
    detect_iso_storage,
    detect_memory_mb,
    detect_net_model,
)
from osx_proxmox_next.infrastructure import CommandResult


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


# ── CpuInfo Detection Tests ──────────────────────────────────────────


def test_detect_cpu_info_amd(monkeypatch, tmp_path):
    """AMD CPUs always need emulated CPU mode."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: AuthenticAMD\n"
        "cpu family\t: 25\n"
        "model\t\t: 97\n"
        "model name\t: AMD Ryzen 9 7950X\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.vendor == "AMD"
    assert info.model_name == "AMD Ryzen 9 7950X"
    assert info.family == 25
    assert info.model == 97
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_intel_legacy(monkeypatch, tmp_path):
    """Pre-hybrid Intel (e.g. Rocket Lake model 167) → host passthrough."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 167\n"
        "model name\t: 11th Gen Intel(R) Core(TM) i9-11900K\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.vendor == "Intel"
    assert info.family == 6
    assert info.model == 167
    assert info.needs_emulated_cpu is False


def test_detect_cpu_info_intel_alder_lake(monkeypatch, tmp_path):
    """Alder Lake (model 151) is hybrid → needs emulation."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 151\n"
        "model name\t: 12th Gen Intel(R) Core(TM) i7-12700K\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.vendor == "Intel"
    assert info.model == 151
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_intel_raptor_lake(monkeypatch, tmp_path):
    """Raptor Lake (model 183) is hybrid → needs emulation."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 183\n"
        "model name\t: 13th Gen Intel(R) Core(TM) i9-13900K\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.vendor == "Intel"
    assert info.model == 183
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_intel_meteor_lake(monkeypatch, tmp_path):
    """Meteor Lake (model 170) is hybrid → needs emulation."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 170\n"
        "model name\t: Intel(R) Core(TM) Ultra 7 155H\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_intel_future_model(monkeypatch, tmp_path):
    """Model >= 190 threshold → assumed hybrid."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 195\n"
        "model name\t: Future Intel CPU\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_intel_comet_lake(monkeypatch, tmp_path):
    """Comet Lake (model 165, 10th gen) is NOT hybrid → host passthrough."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 165\n"
        "model name\t: Intel(R) Core(TM) i9-10900K\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_emulated_cpu is False


def test_detect_cpu_info_missing_cpuinfo(monkeypatch):
    """Missing /proc/cpuinfo → defaults to Intel, host passthrough (safe)."""
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: Path("/nonexistent/cpuinfo"))
    info = detect_cpu_info()
    assert info.vendor == "Intel"
    assert info.model_name == ""
    assert info.family == 0
    assert info.model == 0
    assert info.needs_emulated_cpu is False


def test_detect_cpu_info_multicore_reads_first_block(monkeypatch, tmp_path):
    """Multi-core cpuinfo has repeated blocks; only the first is needed."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 151\n"
        "model name\t: 12th Gen Intel(R) Core(TM) i7-12700K\n"
        "stepping\t: 2\n"
        "\n"
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 151\n"
        "model name\t: 12th Gen Intel(R) Core(TM) i7-12700K\n"
        "stepping\t: 2\n"
        "\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.model == 151
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_non_family_6_intel(monkeypatch, tmp_path):
    """Intel with non-Family 6 (hypothetical) → not hybrid regardless of model."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 19\n"
        "model\t\t: 200\n"
        "model name\t: Hypothetical Intel\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_emulated_cpu is False


# ── Penryn Detection Tests ────────────────────────────────────────────


def test_detect_cpu_info_legacy_intel_broadwell_consumer(monkeypatch, tmp_path):
    """Family 6, model 79 non-Xeon (Broadwell-E Core i7) → needs_penryn=True."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 79\n"
        "model name\t: Intel(R) Core(TM) i7-6950X\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.vendor == "Intel"
    assert info.family == 6
    assert info.model == 79
    assert info.needs_penryn is True
    assert info.needs_emulated_cpu is False
    assert info.is_xeon is False


def test_detect_cpu_info_xeon_e5_v4_no_penryn(monkeypatch, tmp_path):
    """Family 6, model 79 Xeon E5-2640 v4 → is_xeon=True, needs_penryn=False (Xeon excluded from Penryn path)."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 79\n"
        "model name\t: Intel(R) Xeon(R) CPU E5-2640 v4\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.vendor == "Intel"
    assert info.family == 6
    assert info.model == 79
    assert info.is_xeon is True
    assert info.needs_penryn is False
    assert info.needs_emulated_cpu is False


def test_detect_cpu_info_xeon_e5_v3_no_penryn(monkeypatch, tmp_path):
    """Family 6, model 63 Xeon E5-2690 v3 (Haswell-EP) → is_xeon=True, needs_penryn=False."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 63\n"
        "model name\t: Intel(R) Xeon(R) CPU E5-2690 v3\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.is_xeon is True
    assert info.needs_penryn is False
    assert info.needs_emulated_cpu is False


def test_detect_cpu_info_legacy_intel_haswell(monkeypatch, tmp_path):
    """Family 6, model 60 (Haswell) → needs_penryn=True."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 60\n"
        "model name\t: Intel(R) Core(TM) i7-4770K\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_penryn is True
    assert info.needs_emulated_cpu is False


def test_detect_cpu_info_skylake_no_penryn(monkeypatch, tmp_path):
    """Family 6, model 94 (Skylake) is exactly at threshold → needs_penryn=False."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 94\n"
        "model name\t: Intel(R) Core(TM) i7-6700K\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_penryn is False
    assert info.needs_emulated_cpu is False


def test_detect_cpu_info_modern_intel_no_penryn(monkeypatch, tmp_path):
    """Family 6, model 151 (Alder Lake, hybrid) → needs_penryn=False, needs_emulated_cpu=True."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 151\n"
        "model name\t: 12th Gen Intel(R) Core(TM) i7-12700K\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_penryn is False
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_amd_no_penryn(monkeypatch, tmp_path):
    """AMD CPUs never need Penryn mode."""
    fake_cpuinfo = tmp_path / "cpuinfo"
    fake_cpuinfo.write_text(
        "vendor_id\t: AuthenticAMD\n"
        "cpu family\t: 25\n"
        "model\t\t: 97\n"
        "model name\t: AMD Ryzen 9 7950X\n"
    )
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: fake_cpuinfo if p == "/proc/cpuinfo" else Path(p))
    info = detect_cpu_info()
    assert info.needs_penryn is False
    assert info.needs_emulated_cpu is True


def test_detect_cpu_info_unknown_cpu_no_penryn(monkeypatch):
    """model=0 (unknown/missing cpuinfo) → needs_penryn=False (guard condition)."""
    monkeypatch.setattr("osx_proxmox_next.defaults.Path", lambda p: Path("/nonexistent/cpuinfo"))
    info = detect_cpu_info()
    assert info.model == 0
    assert info.needs_penryn is False


# ── detect_net_model Tests ────────────────────────────────────────────


def test_detect_net_model_xeon_returns_e1000():
    """Xeon CPU → e1000-82545em (native macOS driver, no kext needed)."""
    cpu = CpuInfo(vendor="Intel", model_name="Intel(R) Xeon(R) CPU E5-2640 v4",
                  family=6, model=79, needs_emulated_cpu=False,
                  needs_penryn=False, is_xeon=True)
    assert detect_net_model(cpu) == "e1000-82545em"


def test_detect_net_model_penryn_returns_e1000():
    """Pre-Skylake consumer Intel → e1000-82545em."""
    cpu = CpuInfo(vendor="Intel", model_name="Intel(R) Core(TM) i7-4770K",
                  family=6, model=60, needs_emulated_cpu=False,
                  needs_penryn=True, is_xeon=False)
    assert detect_net_model(cpu) == "e1000-82545em"


def test_detect_net_model_modern_intel_returns_vmxnet3():
    """Modern non-Xeon Intel → vmxnet3."""
    cpu = CpuInfo(vendor="Intel", model_name="Intel(R) Core(TM) i9-9900K",
                  family=6, model=158, needs_emulated_cpu=False,
                  needs_penryn=False, is_xeon=False)
    assert detect_net_model(cpu) == "vmxnet3"


def test_detect_net_model_amd_returns_vmxnet3():
    """AMD → vmxnet3 (uses Cascadelake emulation, vmxnet3 kext loads fine)."""
    cpu = CpuInfo(vendor="AMD", model_name="AMD Ryzen 9 7950X",
                  family=25, model=97, needs_emulated_cpu=True,
                  needs_penryn=False, is_xeon=False)
    assert detect_net_model(cpu) == "vmxnet3"


def test_detect_net_model_hybrid_intel_returns_vmxnet3():
    """Hybrid Intel (12th gen+) → vmxnet3."""
    cpu = CpuInfo(vendor="Intel", model_name="12th Gen Intel(R) Core(TM) i7-12700K",
                  family=6, model=151, needs_emulated_cpu=True,
                  needs_penryn=False, is_xeon=False)
    assert detect_net_model(cpu) == "vmxnet3"


# ── ISO Storage Tests ─────────────────────────────────────────────────


class FakePvesm:
    """Mock ProxmoxAdapter for ISO storage tests."""
    def __init__(self, responses=None):
        self._responses = responses or {}

    def pvesm(self, *args):
        key = " ".join(args)
        for pattern, result in self._responses.items():
            if pattern in key:
                return result
        return CommandResult(ok=False, returncode=1, output="not found")


def test_detect_iso_storage_pvesm_fails(monkeypatch):
    """When pvesm is unavailable, fall back to DEFAULT_ISO_DIR."""
    import osx_proxmox_next.services as _ps
    monkeypatch.setattr(_ps, "get_proxmox_adapter", lambda: FakePvesm())
    dirs = detect_iso_storage()
    assert DEFAULT_ISO_DIR in dirs


def test_detect_iso_storage_parses_pvesm(monkeypatch):
    """Parses pvesm status output and resolves paths; skips inactive storage."""
    pvesm_output = (
        "Name         Type     Status           Total            Used       Available        %\n"
        "local          dir     active       100000000        50000000        50000000   50.00%\n"
        "nas-iso        nfs     active       200000000       100000000       100000000   50.00%\n"
        "offline-nas    nfs     inactive     300000000       150000000       150000000   50.00%\n"
    )
    resolve_calls = []
    import osx_proxmox_next.defaults as dm
    original_resolve = dm._resolve_iso_path
    def tracking_resolve(pve, sid):
        resolve_calls.append(sid)
        return original_resolve(pve, sid)

    fake = FakePvesm({"status": CommandResult(ok=True, returncode=0, output=pvesm_output.strip())})
    import osx_proxmox_next.services as _ps
    monkeypatch.setattr(_ps, "get_proxmox_adapter", lambda: fake)
    monkeypatch.setattr(dm, "_resolve_iso_path", tracking_resolve)

    dirs = detect_iso_storage()
    assert DEFAULT_ISO_DIR in dirs
    # inactive storage should NOT be resolved
    assert "offline-nas" not in resolve_calls


def test_resolve_iso_path_local():
    """local storage resolves to default ISO dir."""
    fake = FakePvesm()
    assert _resolve_iso_path(fake, "local") == DEFAULT_ISO_DIR


def test_resolve_iso_path_unknown(monkeypatch):
    """Unknown storage with no pvesm and no /mnt/pve path returns None."""
    fake = FakePvesm()
    monkeypatch.setattr(
        "osx_proxmox_next.defaults.Path",
        lambda p: Path("/nonexistent") if "/mnt/pve/" in str(p) else Path(p),
    )
    assert _resolve_iso_path(fake, "nonexistent-storage") is None


def test_resolve_iso_path_mnt_pve(tmp_path, monkeypatch):
    """Resolves storage via /mnt/pve/{id}/template/iso if it exists."""
    iso_dir = tmp_path / "template" / "iso"
    iso_dir.mkdir(parents=True)
    fake = FakePvesm()
    monkeypatch.setattr(
        "osx_proxmox_next.defaults.Path",
        lambda p: iso_dir if "/mnt/pve/" in str(p) else Path(p),
    )
    result = _resolve_iso_path(fake, "my-nas")
    assert result == str(iso_dir)


def test_resolve_iso_path_pvesm_success(monkeypatch):
    """pvesm path returns a file path; we extract the parent directory."""
    fake = FakePvesm({"path": CommandResult(ok=True, returncode=0, output="/mnt/pve/nas/template/iso/probe.iso")})
    result = _resolve_iso_path(fake, "nas")
    assert result == "/mnt/pve/nas/template/iso"


def test_detect_iso_storage_resolves_path(monkeypatch):
    """detect_iso_storage resolves storage IDs via _resolve_iso_path."""
    pvesm_output = (
        "Name         Type     Status           Total            Used       Available        %\n"
        "nas-iso        nfs     active       200000000       100000000       100000000   50.00%\n"
    )
    fake = FakePvesm({
        "status": CommandResult(ok=True, returncode=0, output=pvesm_output.strip()),
        "path": CommandResult(ok=True, returncode=0, output="/mnt/pve/nas-iso/template/iso/probe.iso"),
    })
    import osx_proxmox_next.services as _ps
    monkeypatch.setattr(_ps, "get_proxmox_adapter", lambda: fake)
    dirs = detect_iso_storage()
    assert "/mnt/pve/nas-iso/template/iso" in dirs
    assert DEFAULT_ISO_DIR in dirs
