"""The pure parts of the TUI: PPM sampling, size presets, fix commands, driver log, VM records, form rules."""
from __future__ import annotations

import pytest
from rich.segment import Segment

from osx_proxmox_next.infrastructure import CommandResult
from osx_proxmox_next.models.wizard_state import NewMacState, default_name, effective_size, validate_new_mac
from osx_proxmox_next.preflight import PreflightCheck, _check_ignore_msrs, run_preflight
from osx_proxmox_next.presets import build_presets, preset_fits, preset_label
from osx_proxmox_next.services import detection_service as ds
from osx_proxmox_next.services.fix_service import collect_fixes, run_fix_commands
from osx_proxmox_next.services.unattended_service import (
    driver_alive,
    driver_log_path,
    parse_driver_log,
    read_driver_state,
    spawn_unattended_driver,
)
from osx_proxmox_next.unattended import PHASE_MARK, PHASES, QmConsole
from osx_proxmox_next.widgets.live_screen import render_cells
from osx_proxmox_next.widgets.ppm import fit_size, parse_ppm, sample
from tui_fakes import desktop_ppm


# ── PPM ──────────────────────────────────────────────────────────────

def ppm(width: int, height: int, pixel=(10, 20, 30), header: bytes = b"") -> bytes:
    return b"P6\n" + header + b"%d %d\n255\n" % (width, height) + bytes(pixel) * (width * height)


def test_parse_ppm_reads_size_and_offset():
    frame = parse_ppm(ppm(4, 2))
    assert (frame.width, frame.height) == (4, 2) and frame.offset == len(b"P6\n4 2\n255\n")


def test_parse_ppm_skips_comments():
    frame = parse_ppm(ppm(2, 2, header=b"# made by qemu\n"))
    assert frame is not None and frame.width == 2


@pytest.mark.parametrize("data", [
    b"", b"P5\n2 2\n255\n" + b"\0" * 4, b"P6\nx 2\n255\n", b"P6\n2 2\n65535\n" + b"\0" * 24,
    b"P6\n2 2\n255\n" + b"\0" * 5, b"P6\n0 2\n255\n", b"P6\n2",
])
def test_parse_ppm_rejects_what_is_not_a_complete_frame(data):
    assert parse_ppm(data) is None


def test_fit_size_keeps_the_aspect_ratio_inside_the_box():
    assert fit_size(1920, 1080, 60, 40) == (60, 34)
    assert fit_size(1920, 1080, 100, 20) == (36, 20)  # height is the limit
    assert fit_size(100, 100, 0, 5) == (0, 0)


def test_sample_averages_source_pixels():
    data = b"P6\n2 2\n255\n" + bytes([0, 0, 0, 100, 100, 100, 200, 200, 200, 100, 100, 100])
    frame = parse_ppm(data)
    assert sample(frame, 1, 1) == [[(100, 100, 100)]]
    assert sample(frame, 2, 2)[1][0] == (200, 200, 200)


def test_sample_a_big_frame_stays_small_and_exact_for_a_flat_colour():
    frame = parse_ppm(desktop_ppm(320, 200))
    grid = sample(frame, 40, 20)
    assert len(grid) == 20 and len(grid[0]) == 40


def test_render_cells_draws_two_pixels_per_cell_and_letterboxes():
    pixels = [[(255, 0, 0), (255, 0, 0)], [(0, 0, 255), (0, 0, 255)]]
    strips = render_cells(pixels, cols=4, rows=1)
    assert len(strips) == 1 and all(s.cell_length == 4 for s in strips)
    seg: Segment = list(strips[0])[1]  # inside the picture: top red, bottom blue
    assert seg.text == "▀" and seg.style.color.triplet == (255, 0, 0) and seg.style.bgcolor.triplet == (0, 0, 255)
    edge: Segment = list(strips[0])[0]  # outside: black
    assert edge.style.color.triplet == (0, 0, 0)


# ── presets ──────────────────────────────────────────────────────────

def test_presets_light_balanced_heavy():
    p = build_presets("sequoia", 8, 16384, host_cores=32, host_memory_mb=65536)
    assert (p["light"].cores, p["light"].memory_gb, p["light"].disk_gb) == (4, 8, 64)
    assert (p["balanced"].cores, p["balanced"].memory_gb, p["balanced"].disk_gb) == (8, 16, 128)
    assert (p["heavy"].cores, p["heavy"].memory_gb, p["heavy"].disk_gb) == (16, 32, 256)
    assert p["heavy"].label == "Heavy" and preset_label("custom") == "Custom"


def test_heavy_is_capped_by_the_host_and_rounded_down_to_a_power_of_two():
    p = build_presets("tahoe", 8, 16384, host_cores=12, host_memory_mb=24 * 1024)
    assert p["heavy"].cores == 8       # min(16, 12) rounded down
    assert p["heavy"].memory_mb == 12 * 1024  # half of the host RAM
    assert p["balanced"].disk_gb == 160


def test_unknown_host_figures_do_not_cap():
    p = build_presets("sonoma", 4, 8192, host_cores=0, host_memory_mb=0)
    assert p["heavy"].cores == 8 and p["heavy"].memory_mb == 16384


def test_a_preset_that_does_not_fit_the_host():
    p = build_presets("sequoia", 8, 16384, host_cores=32, host_memory_mb=65536)
    assert preset_fits(p["balanced"], 32, 60000, 412)
    assert not preset_fits(p["heavy"], 8, 60000, 412)            # more cores than the host has
    assert not preset_fits(p["heavy"], 32, 20000, 412)           # not enough free RAM
    assert not preset_fits(p["heavy"], 32, 60000, 200)           # not enough disk
    assert preset_fits(p["heavy"], 0, 0, None)                   # nothing known: nothing blocks


# ── preflight fixes ──────────────────────────────────────────────────

def test_ignore_msrs_fix_is_the_live_write_the_persist_line_and_the_initramfs(tmp_path):
    sysfs = tmp_path / "ignore_msrs"
    sysfs.write_text("N\n")
    check = _check_ignore_msrs(tmp_path / "kvm.conf", sysfs)
    assert [c.display for c in check.fix] == [
        "echo Y > /sys/module/kvm/parameters/ignore_msrs",
        "echo 'options kvm ignore_msrs=Y' >> /etc/modprobe.d/kvm.conf",
        "update-initramfs -k all -u"]
    assert check.why.startswith("macOS hangs at the Apple logo")


def test_ignore_msrs_fix_skips_the_persist_line_when_it_is_already_there(tmp_path):
    sysfs, conf = tmp_path / "ignore_msrs", tmp_path / "kvm.conf"
    sysfs.write_text("N\n")
    conf.write_text("options kvm ignore_msrs=Y\n")
    check = _check_ignore_msrs(conf, sysfs)
    assert [c.display for c in check.fix] == ["echo Y > /sys/module/kvm/parameters/ignore_msrs"]


def test_ignore_msrs_has_no_fix_without_a_kvm_module(tmp_path):
    assert _check_ignore_msrs(tmp_path / "kvm.conf", tmp_path / "missing").fix == ()


def test_ok_ignore_msrs_has_no_fix(tmp_path):
    sysfs = tmp_path / "ignore_msrs"
    sysfs.write_text("Y\n")
    check = _check_ignore_msrs(tmp_path / "kvm.conf", sysfs)
    assert check.ok and check.fix == ()


def test_a_missing_build_tool_offers_its_apt_package(monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.preflight._find_binary", lambda cmd: None if cmd == "sgdisk" else "/bin/" + cmd)
    checks = {c.name: c for c in run_preflight()}
    fix = checks["sgdisk available"].fix
    assert [c.display for c in fix] == ["apt-get install -y gdisk"]
    assert fix[0].argv == ("apt-get", "install", "-y", "gdisk")
    assert checks["qm available"].fix == ()
    assert checks["sgdisk available"].why == "Needed to partition the OpenCore boot disk."


def test_checks_without_a_safe_fix_carry_only_an_explanation(monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.preflight._find_binary", lambda cmd: "/bin/" + cmd)
    monkeypatch.setattr("osx_proxmox_next.preflight._is_root", lambda: False)
    checks = {c.name: c for c in run_preflight()}
    assert not checks["Root privileges"].ok and checks["Root privileges"].fix == ()
    assert "root" in checks["Root privileges"].why
    assert checks["/dev/kvm present"].fix == () and checks["/dev/kvm present"].why
    assert PreflightCheck("mystery", False, "x").why == ""


def test_collect_fixes_lists_each_command_once_in_order():
    from tui_fakes import failing_checks
    commands = collect_fixes(failing_checks())
    assert [c.display for c in commands][-1] == "apt install -y dmg2img" and len(commands) == 4
    assert collect_fixes([PreflightCheck("x", True, "", fix=commands[:1])]) == []
    twice = [PreflightCheck("a", False, "", fix=commands[:1]), PreflightCheck("b", False, "", fix=commands[:1])]
    assert len(collect_fixes(twice)) == 1


def test_run_fix_commands_streams_output_and_stops_at_the_first_failure():
    from osx_proxmox_next.preflight import FixCommand
    seen: list[str] = []

    class Proc:
        def __init__(self, argv, **kw):
            self.argv = argv
            self.stdout = iter(["line one\n", "line two\n"])

        def wait(self):
            return 1 if self.argv[0] == "bad" else 0

    cmds = [FixCommand("good", ("good",)), FixCommand("bad", ("bad",)), FixCommand("never", ("never",))]
    assert run_fix_commands(cmds, seen.append, popen=Proc) is False
    assert seen[0] == "$ good" and "line two" in seen and seen[-1].startswith("It exited with code 1")
    assert not any("never" in line for line in seen)
    seen.clear()
    assert run_fix_commands(cmds[:1], seen.append, popen=Proc) is True


def test_run_fix_commands_reports_a_command_that_cannot_start():
    from osx_proxmox_next.preflight import FixCommand
    seen: list[str] = []

    def boom(*a, **k):
        raise OSError("no such file")

    assert run_fix_commands([FixCommand("x", ("x",))], seen.append, popen=boom) is False
    assert "Could not start it" in seen[-1]


# ── the install driver's log ─────────────────────────────────────────

LOG = "\n".join([
    "BETA: unattended install for VM 105 (128 GB target disk).",
    "The VM disk will be ERASED. Watch progress below or on the VM console.",
    PHASE_MARK + "recovery 1700000000",
    "  [13:30:01] Waiting for the OpenCore boot picker",
    PHASE_MARK + "erase 1700000100",
    PHASE_MARK + "installer 1700000200",
    "  [13:40:02] First reboot reached; detaching recovery so the picker has one entry",
    "  [13:42:09] 1080p frame 1: confirmed the only boot entry",
])


def test_parse_driver_log_reads_phases_events_and_the_start_time():
    state = parse_driver_log(LOG)
    assert state.seen == ["recovery", "erase", "installer"] and state.current == "installer"
    assert state.started_at == 1700000000.0 and not state.finished and state.error == ""
    assert state.events[-1] == ("13:42:09", "1080p frame 1: confirmed the only boot entry")


def test_parse_driver_log_knows_when_the_install_is_ready_or_failed():
    done = parse_driver_log(LOG + "\n" + PHASE_MARK + "ready 1700003000\n  [14:00:00] Done: macOS is quiet")
    assert done.finished and done.current == "ready"
    failed = parse_driver_log(LOG + "\nERROR: Timed out after 900s waiting for recovery to start")
    assert failed.error.startswith("Timed out") and not failed.installing


def test_parse_driver_log_only_counts_the_newest_run():
    two_runs = LOG + "\nERROR: old failure\n" + LOG.replace("recovery 1700000000", "recovery 1700009999")
    state = parse_driver_log(two_runs)
    assert state.error == "" and state.started_at == 1700009999.0


def test_parse_driver_log_ignores_unknown_phases_and_noise():
    state = parse_driver_log(PHASE_MARK + "bogus 1\n" + PHASE_MARK + "\nrandom text\n" + PHASE_MARK + "recovery")
    assert state.seen == ["recovery"] and state.started_at == 0.0


def test_read_driver_state_combines_the_log_and_the_process(tmp_path, monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.services.unattended_service.LOG_DIR", str(tmp_path))
    assert read_driver_state(105).exists is False
    (tmp_path / "osx-next-unattended-105.log").write_text(LOG)
    monkeypatch.setattr("osx_proxmox_next.services.unattended_service.driver_alive", lambda vmid: True)
    state = read_driver_state(105)
    assert state.installing and state.alive and state.current == "installer"
    monkeypatch.setattr("osx_proxmox_next.services.unattended_service.driver_alive", lambda vmid: False)
    assert not read_driver_state(105).installing


def test_read_driver_state_without_epochs_uses_the_file_time(tmp_path, monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.services.unattended_service.LOG_DIR", str(tmp_path))
    (tmp_path / "osx-next-unattended-7.log").write_text(PHASE_MARK + "recovery\n")
    monkeypatch.setattr("osx_proxmox_next.services.unattended_service.driver_alive", lambda vmid: True)
    assert read_driver_state(7).started_at > 0


def test_driver_alive_scans_proc_for_the_command_line(tmp_path):
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "123" / "cmdline").write_bytes(
        "\0".join(["python", "-m", "osx_proxmox_next.cli", "install-unattended", "--vmid", "105", "--disk-gb", "128", ""]).encode())
    (proc / "456").mkdir()
    (proc / "456" / "cmdline").write_bytes(b"bash\0")
    (proc / "789").mkdir()  # no cmdline file
    (proc / "self").mkdir()
    assert driver_alive(105, str(proc)) and not driver_alive(106, str(proc))
    assert driver_alive(105, str(tmp_path / "missing")) is False


def test_spawn_unattended_driver_starts_a_detached_process(tmp_path, monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.services.unattended_service.LOG_DIR", str(tmp_path))
    calls = {}
    path = spawn_unattended_driver(105, 128, popen=lambda argv, **kw: calls.update(argv=argv, kw=kw))
    assert path == driver_log_path(105) == str(tmp_path / "osx-next-unattended-105.log")
    assert "install-unattended" in calls["argv"] and calls["argv"][-4:] == ["--vmid", "105", "--disk-gb", "128"]
    assert calls["kw"]["start_new_session"] is True


def test_qm_console_can_grab_into_its_own_file(tmp_path):
    ran = []

    def runner(cmd, **kw):
        ran.append((cmd, kw))
        probe = cmd.split("screendump ")[1].split("'")[0]
        open(probe, "wb").write(desktop_ppm(8, 8))

    own = str(tmp_path / "view.ppm")
    console = QmConsole(105, runner=runner, sleep=lambda s: None, probe_path=own)
    assert parse_ppm(console.grab()) is not None
    assert own in ran[0][0] and ran[0][1]["start_new_session"] is True
    assert QmConsole(105)._probe.endswith("osx-next-unattended-105.ppm")


def test_phase_ids_match_the_sub_steps():
    from osx_proxmox_next.services.unattended_service import SUBSTEPS
    assert [s[0] for s in SUBSTEPS] == list(PHASES)


# ── VM records ───────────────────────────────────────────────────────

CFG = """acpi: 1
args: -device isa-applesmc,osk="x"
cores: 8
memory: 16384
name: macos-sequoia
net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr1,tag=20,firewall=0
virtio0: local-lvm:vm-105-disk-1,iothread=1,size=128G
ide0: local:iso/oc.iso,media=disk,size=40M
sata0: none,media=cdrom
hostpci0: 0000:0a:00,pcie=1
vga: none
"""


def test_parse_vm_config_reads_what_the_tui_shows():
    facts = ds.parse_vm_config(CFG)
    assert (facts.name, facts.cores, facts.memory_mb) == ("macos-sequoia", 8, 16384)
    assert (facts.bridge, facts.nic_model, facts.vlan) == ("vmbr1", "vmxnet3", "20")
    assert facts.disks[0] == ds.DiskFact("virtio0", "local-lvm", 128)
    assert facts.has_gpu and facts.display_on_gpu


def test_parse_vm_config_defaults_for_a_bare_config():
    facts = ds.parse_vm_config("name: x\n")
    assert facts.cores == 0 and facts.disks == [] and not facts.has_gpu and facts.bridge == ""
    assert not ds.parse_vm_config("hostpci0: 0000:0a:00\nvga: std\n").display_on_gpu


class FakeAdapter:
    def __init__(self, configs, listing=None, ok=True):
        self.configs = configs
        self.listing = listing
        self.ok = ok

    def qm(self, *args):
        if args[0] == "list":
            text = self.listing or "VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n" + "\n".join(
                f"{vmid} vm{vmid} {st} 16384 128 0" for vmid, (st, _c) in self.configs.items())
            return CommandResult(self.ok, 0 if self.ok else 1, text)
        status, cfg = self.configs[int(args[1])]
        return CommandResult(True, 0, cfg)

    def pvesm(self, *args):
        return CommandResult(True, 0, "Name Type Status Total Used Available %\nlocal-lvm lvmthin active 1000 100 433061888 10%\nnfs nfs inactive 1 1 1 1%\n")


def test_list_macos_vm_records_returns_structured_rows_in_qm_order():
    adapter = FakeAdapter({101: ("running", CFG), 102: ("stopped", "name: plain\n"), 104: ("stopped", CFG.replace("8\n", "4\n", 1))})
    records = ds.list_macos_vm_records(adapter)
    assert [r.vmid for r in records] == [101, 104]
    first = records[0]
    assert (first.name, first.status, first.cores, first.memory_gb) == ("macos-sequoia", "running", 8, 16)
    assert first.facts.has_gpu
    assert ds.list_macos_vms(adapter)[0].startswith("VMID") and len(ds.list_macos_vms(adapter)) == 3


def test_list_macos_vm_records_is_empty_when_qm_fails():
    assert ds.list_macos_vm_records(FakeAdapter({}, ok=False)) == []
    assert ds.list_macos_vms(FakeAdapter({}, listing="", ok=True)) == []


def test_detect_storage_free_gb_reads_the_available_column():
    assert ds.detect_storage_free_gb(FakeAdapter({})) == {"local-lvm": 413}

    class Down(FakeAdapter):
        def pvesm(self, *a):
            return CommandResult(False, 1, "")

    assert ds.detect_storage_free_gb(Down({})) == {}


# ── the New Mac rules ────────────────────────────────────────────────

PRESETS = build_presets("sequoia", 8, 16384, 32, 65536)


def state(**kw) -> NewMacState:
    base = dict(vmid="105", storage="local-lvm", iso_dir="/var/lib/vz/template/iso")
    base.update(kw)
    return NewMacState(**base)


def test_a_name_the_user_did_not_edit_follows_the_version():
    st = state()
    assert st.name == "macos-sequoia"
    st.set_macos("tahoe")
    assert st.name == default_name("tahoe") == "macos-tahoe"
    st.name, st.name_edited = "my-mac", True
    st.set_macos("sonoma")
    assert st.name == "my-mac" and st.macos == "sonoma"


def test_effective_size_comes_from_the_preset_or_the_custom_fields():
    assert effective_size(state(size="light"), PRESETS) == ("4", "8192", "64")
    assert effective_size(state(size="balanced"), PRESETS) == ("8", "16384", "128")
    assert effective_size(state(size="custom", custom_cores="6", custom_memory_gb="12", custom_disk_gb="90"),
                          PRESETS) == ("6", "12288", "90")
    assert effective_size(state(size="custom", custom_memory_gb="x"), PRESETS)[1] == "x"


def test_a_good_form_makes_a_config_with_memory_in_mb():
    errors, config = validate_new_mac(state(size="custom", custom_memory_gb="12"), PRESETS)
    assert errors == {} and config.memory_mb == 12288 and config.macos == "sequoia"
    assert config.net_model == "vmxnet3" and config.cpu_model == ""
    errors, config = validate_new_mac(state(e1000=True, use_penryn=True), PRESETS)
    assert config.net_model == "e1000-82545em" and config.cpu_model == "Penryn"


def test_errors_are_worded_in_gb_and_come_in_screen_order():
    errors, config = validate_new_mac(
        state(name="a", size="custom", custom_memory_gb="2", custom_disk_gb="10", bridge="br0"), PRESETS)
    assert config is None
    assert list(errors) == ["name", "memory", "disk", "bridge"]
    assert errors["memory"] == "Memory must be a whole number of GB, at least 4."
    assert errors["disk"] == "Disk must be a whole number of GB, at least 64."
    assert errors["name"] == "Name must be at least 3 characters."


def test_host_memory_limit_is_worded_in_gb():
    errors, _ = validate_new_mac(state(size="custom", custom_memory_gb="40"), PRESETS, host_memory_limit_mb=30 * 1024)
    assert "only 30 GB free" in errors["memory"] and "MB" not in errors["memory"]


def test_garbage_numbers_are_reported_not_raised():
    errors, config = validate_new_mac(state(size="custom", custom_cores="many", custom_memory_gb="lots"), PRESETS)
    assert config is None and "cores" in errors and "memory" in errors


def test_config_issues_map_to_their_rows():
    errors, _ = validate_new_mac(state(size="custom", custom_cores="1", vlan="9000", installer_path="a b",
                                       storage="bad name!"), PRESETS)
    assert {"cores", "vlan", "installer_path", "storage"} <= set(errors)
    errors, _ = validate_new_mac(state(apple_services=True, vmgenid="nope", mac="zz"), PRESETS)
    assert {"vmgenid", "mac"} <= set(errors)
    errors, _ = validate_new_mac(state(existing_uuid="not-a-uuid"), PRESETS)
    assert "existing_uuid" in errors


# ── what the install driver writes to its log ────────────────────────

def test_the_cli_logs_phase_markers_with_an_epoch_and_events_with_a_clock(capsys):
    from osx_proxmox_next.cli import _log_unattended_event
    _log_unattended_event(PHASE_MARK + "erase")
    _log_unattended_event("Installer launched")
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(PHASE_MARK + "erase ") and out[0].split()[-1].isdigit()
    assert out[1].startswith("  [") and out[1].endswith("] Installer launched")
    state = parse_driver_log("BETA: unattended install for VM 1\n" + "\n".join(out))
    assert state.seen == ["erase"] and state.events[0][1] == "Installer launched" and state.started_at > 0


# ── what the New Mac screen asks the host ────────────────────────────

def test_host_facts_gathers_every_detection(monkeypatch):
    from osx_proxmox_next.services import host_facts as hf
    monkeypatch.setattr(hf, "detect_storage_targets", lambda: ["local-lvm"])
    monkeypatch.setattr(hf, "detect_iso_storage", lambda: ["/iso"])
    monkeypatch.setattr(hf, "detect_storage_free_gb", lambda: {"local-lvm": 10})
    monkeypatch.setattr(hf, "detect_host_cores", lambda: 12)
    monkeypatch.setattr(hf, "detect_total_memory_mb", lambda: 32768)
    monkeypatch.setattr(hf, "max_vm_memory_mb", lambda: 30000)
    monkeypatch.setattr(hf, "detect_next_vmid", lambda: 321)
    facts = hf.load_host_facts()
    assert (facts.storage_targets, facts.iso_dir, facts.storage_free_gb) == (["local-lvm"], "/iso", {"local-lvm": 10})
    assert (facts.host_cores, facts.host_memory_mb, facts.vm_memory_limit_mb, facts.next_vmid) == (12, 32768, 30000, 321)
    facts.iso_dirs = []
    assert facts.iso_dir == "/var/lib/vz/template/iso"


def test_new_host_detections_read_the_system(monkeypatch):
    from osx_proxmox_next import defaults
    monkeypatch.setattr(defaults.os, "cpu_count", lambda: 24)
    assert defaults.detect_host_cores() == 24
    monkeypatch.setattr(defaults.os, "cpu_count", lambda: None)
    assert defaults.detect_host_cores() == 0
    monkeypatch.setattr(defaults, "_meminfo_mb", lambda field: 4096 if field == "MemTotal" else 0)
    assert defaults.detect_total_memory_mb() == 4096
