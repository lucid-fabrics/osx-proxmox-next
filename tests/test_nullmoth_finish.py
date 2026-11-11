"""nullmoth_finish: download, stage from recovery, attach, first boot with the driver install, report."""
from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest

from osx_proxmox_next import igpu_finish, nullmoth_engine as engine, nullmoth_finish as nf
from osx_proxmox_next.domain import PlanStep
from osx_proxmox_next.downloader import DownloadError
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.infrastructure import CommandResult
from osx_proxmox_next.nullmoth import AttachPlan
from test_igpu_finish import CFG as IGPU_CFG, Clock, Console, Pve as BasePve

CFG = {**IGPU_CFG, "machine": "q35", "name": "macos-sequoia", "net0": "vmxnet3=BC:24:11:AA:BB:CC,bridge=vmbr0"}
VERIFIED = "verified: kexts=5 display=no ip=10.0.0.9"


class Pve(BasePve):
    def __init__(self, cfg=None, **kw):
        super().__init__(CFG if cfg is None else cfg, **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = SimpleNamespace(applied=[], stage="staged", guest=deque(["installed", VERIFIED]), attach_ok=True,
                        plan_issues=[], terminal=None, pve=None, events=[], tarball=tmp_path / "nullmoth-nvidia-2.9.tar.gz",
                        pins={"version": "620.70.06", "url": "u", "sha256": "f" * 64, "name": "fw.run"}, fw_error=None,
                        payload_ok=True, resolve_error=None, built=[])
    for name in ("a.zip", "rec.img"):
        (tmp_path / name).write_bytes(b"x")
    e.tarball.write_bytes(b"t")
    monkeypatch.setattr(nf, "ensure_amfipass", lambda iso: tmp_path / "a.zip")
    monkeypatch.setattr(nf, "resolve_recovery_or_installer_path", lambda cfg: tmp_path / "rec.img")
    monkeypatch.setattr(nf, "create_snapshot", lambda vmid, adapter=None: SimpleNamespace(path=tmp_path / "snap"))

    def resolve(choice, get):
        if e.resolve_error:
            raise engine.ReleaseError(e.resolve_error)
        return {"tag": "v2.9" if choice == "latest" else choice, "source": "latest" if choice == "latest" else "chosen"}
    monkeypatch.setattr(engine, "resolve", resolve)
    monkeypatch.setattr(engine, "fetch_release", lambda pick, d, read, fetch: (e.tarball, "a" * 64))
    monkeypatch.setattr(engine, "firmware_pins", lambda tarball: e.pins)

    def fw(pins, d, fetch):
        if e.fw_error:
            raise engine.ReleaseError(e.fw_error)
        return tmp_path / pins["name"]
    monkeypatch.setattr(engine, "fetch_firmware", fw)
    monkeypatch.setattr(engine, "build_payload", lambda out, files: e.built.append(sorted(files)) or (e.payload_ok, "disk full"))

    def reader(path, run=None):
        starts = sum(1 for c in e.pve.calls if c[:2] == ("start", "9"))
        if starts <= 1:
            return e.stage
        return e.guest[0] if len(e.guest) == 1 else e.guest.popleft() if e.guest else ""
    monkeypatch.setattr(igpu_finish.igpu_engine, "read_result", reader)

    def plan(vmid, gpu, zip_path, macos=None, adapter=None, sysfs_root=None):
        p = AttachPlan(9)
        p.issues, p.warnings, p.steps = list(e.plan_issues), ["a note"], [PlanStep(title="Attach the card", argv=["true"])]
        return p
    monkeypatch.setattr(nf.nullmoth, "prepare_attach", plan)

    def apply(steps, execute=True, adapter=None, **k):
        e.applied.append([s.title for s in steps])
        ok = e.attach_ok or not steps or "picker" in steps[0].title
        return ApplyResult(ok, [] if ok else [StepResult("t", "c", False, 1, "")], tmp_path / "log")
    monkeypatch.setattr(nf, "apply_plan", apply)
    monkeypatch.setattr(igpu_finish, "apply_plan", apply)
    monkeypatch.setattr(igpu_finish, "open_recovery_terminal",
                        lambda console, on_event, clock, sleep: (_ for _ in ()).throw(e.terminal) if e.terminal else None)
    e.tmp = tmp_path
    return e


def go(env, pve=None, console=None, find=lambda mac: "", probe=lambda ip, port: True, **kw):
    clock, env.pve = Clock(), pve or Pve()
    res = nf.finish(9, "0000:01:00.0", kw.pop("choice", "latest"), console=console or Console(), pve=env.pve,
                    iso_dir=env.tmp, on_event=env.events.append, clock=clock.monotonic, sleep=clock.sleep,
                    work_dir=env.tmp, find_ip=find, probe=probe, **kw)
    return res, env.pve


def test_the_whole_setup(env):
    console = Console()
    res, pve = go(env, console=console, find=lambda mac: "10.0.0.7" if mac == "bc:24:11:aa:bb:cc" else "")
    assert res.verified and res.version == "v2.9" and res.kexts == 5 and res.display == "no"
    assert res.ip == "10.0.0.7" and res.ssh_up and res.vnc_up  # the host's table wins over what the guest reported
    assert res.notes == ["a note"]
    assert console.typed == [igpu_finish.igpu_engine.STAGE_COMMAND] and console.keys == ["ret"]
    assert env.built == [sorted(["stage.sh", "firstboot.sh", engine.LAUNCHD_PLIST, env.tarball.name, "fw.run"])]
    # recovery and payload disks are gone again, the boot order is OpenCore first, and the VM runs
    assert not any(k.startswith("sata") for k in pve.cfg) and pve.cfg["boot"] == "order=ide0;virtio0"
    assert pve.status == "running"
    assert ("start", "9") == [c for c in pve.calls if c[0] == "start"][-1]
    assert [t[0] for t in env.applied if t][0:2] == ["Make the OpenCore picker show recovery only and wait for a key",
                                                  "Show every volume in the OpenCore picker again"]
    assert not (env.tmp / "nullmoth-payload-9.img").exists()
    assert any("downloading" in e for e in env.events) and any("NullMoth driver inside macOS" in e for e in env.events)
    assert any("The driver is installed" in e for e in env.events) and any("address" in e for e in env.events)


def test_the_guest_address_falls_back_to_what_the_guest_reported(env):
    res, _ = go(env)
    assert res.ip == "10.0.0.9" and res.ssh_up


def test_no_address_and_no_ssh_end_the_wait(env):
    env.guest = deque(["verified: kexts=4 display=yes ip="])
    res, _ = go(env, probe=lambda ip, port: False)
    assert res.verified and res.ip == "" and not res.ssh_up
    env.guest = deque([VERIFIED])
    res, _ = go(env, probe=lambda ip, port: False)
    assert res.ip == "10.0.0.9" and not res.ssh_up and not res.vnc_up


def test_a_chosen_release_and_a_firmware_download_failure(env):
    env.fw_error = "no network"
    res, _ = go(env, choice="v2.8")
    assert res.version == "v2.8" and any("no network" in n for n in res.notes)
    assert env.built[0] == sorted(["stage.sh", "firstboot.sh", engine.LAUNCHD_PLIST, env.tarball.name])
    env.pins = None
    res, _ = go(env)
    assert any("does not pin" in n for n in res.notes)


def test_a_failed_install_is_reported_with_the_reason(env):
    env.guest = deque(["failed: the driver installer stopped: STOP macOS 14"])
    res, pve = go(env)
    assert not res.verified and "STOP macOS 14" in res.detail and pve.calls[-1] == ("start", "9")
    assert res.ip == ""


def test_no_final_report_is_noted_and_the_vm_is_stopped(env):
    env.guest = deque(["installed"])
    res, pve = go(env, pve=Pve(halts=(1, 10**6, 1)))
    assert not res.verified and any("no final report" in n and "installed" in n and "kmutil showloaded" in n for n in res.notes)
    assert ("stop", "9") in pve.calls


def test_a_restart_that_hangs_is_kicked_once(env):
    seq = deque(["installed"] * 20 + [VERIFIED])
    env.guest = seq
    res, pve = go(env)
    assert res.verified and any("restart hung" in e for e in env.events)
    assert len([c for c in pve.calls if c[:2] == ("stop", "9")]) >= 1


def test_the_guest_that_never_powers_off_is_stopped_after_its_result(env):
    res, pve = go(env, pve=Pve(halts=(1, 10**6, 1)))
    assert res.verified and ("stop", "9") in pve.calls


def test_the_picker_timeout_failure_is_reported_not_fatal(env):
    res, _ = go(env, console=Console(restore_error="no mount"))
    assert res.verified and any("no mount" in n for n in res.notes)


@pytest.mark.parametrize("change,text", [
    ({"hostpci0": "0000:06:00,pcie=1"}, "nvidia-detach"),
])
def test_vm_checks(env, change, text):
    with pytest.raises(nf.FinishError, match=text):
        go(env, pve=Pve({**CFG, **change}))


def test_a_running_and_a_missing_vm_are_refused(env):
    with pytest.raises(nf.FinishError, match="running"):
        go(env, pve=Pve(status="running"))

    class Gone(Pve):
        def qm(self, *a):
            return CommandResult(False, 2, "no such VM") if a[0] == "status" else super().qm(*a)
    with pytest.raises(nf.FinishError, match="not found"):
        go(env, pve=Gone())


def test_a_refused_plan_stops_before_any_download(env):
    env.plan_issues = ["card is in use"]
    pve = Pve()
    with pytest.raises(nf.FinishError, match="Cannot attach the card: card is in use"):
        go(env, pve=pve)
    assert env.built == [] and not any(c[0] == "set" for c in pve.calls)


def test_release_and_amfipass_failures_change_nothing(env, monkeypatch):
    env.resolve_error = "GitHub refused the request"
    pve = Pve()
    with pytest.raises(nf.FinishError, match="refused"):
        go(env, pve=pve)
    assert not any(c[0] == "set" for c in pve.calls)

    def boom(iso):
        raise DownloadError("offline")
    monkeypatch.setattr(nf, "ensure_amfipass", boom)
    with pytest.raises(nf.FinishError, match="Could not fetch AMFIPass: offline"):
        go(env)


def test_payload_and_import_failures(env):
    env.payload_ok = False
    with pytest.raises(nf.FinishError, match="disk full"):
        go(env)
    env.payload_ok = True
    with pytest.raises(nf.FinishError, match="Could not import"):
        go(env, pve=Pve(import_ok=False))


def test_a_missing_recovery_image_is_downloaded(env, monkeypatch):
    monkeypatch.setattr(nf, "resolve_recovery_or_installer_path", lambda cfg: env.tmp / "absent.img")
    seen = []
    monkeypatch.setattr("osx_proxmox_next.downloader.download_recovery",
                        lambda version, iso: seen.append(version) or env.tmp / "rec.img")
    assert go(env)[0].verified and seen == ["sequoia"]


def test_staging_failure_cleans_up_and_puts_the_vm_back(env):
    env.stage = "failed: the installed macOS data volume is not mounted"
    pve = Pve()
    with pytest.raises(nf.FinishError, match="FileVault"):
        go(env, pve=pve)
    assert not any(k.startswith("sata") for k in pve.cfg)
    assert pve.cfg["vga"] == "std" and "boot" not in pve.cfg and pve.cfg["args"] == CFG["args"]
    assert env.applied[-1] == ["Show every volume in the OpenCore picker again"]
    assert not (env.tmp / "nullmoth-payload-9.img").exists()


def test_the_attach_is_refused_or_fails_after_staging(env):
    env.attach_ok = False
    with pytest.raises(nf.FinishError, match="Attaching the card failed.*nvidia-detach 9"):
        go(env)


def test_the_attach_plan_is_checked_again_after_staging(env, monkeypatch):
    calls = []

    def plan(vmid, gpu, zip_path, macos=None, adapter=None, sysfs_root=None):
        calls.append(1)
        p = AttachPlan(9)
        p.issues = ["card taken"] if len(calls) == 2 else []
        return p
    monkeypatch.setattr(nf.nullmoth, "prepare_attach", plan)
    with pytest.raises(nf.FinishError, match="card taken"):
        go(env)


def test_the_vm_that_will_not_start_is_reported(env):
    with pytest.raises(nf.FinishError, match="Could not start"):
        go(env, pve=Pve(start_ok=False))


def test_unknown_release_name_needs_the_version(env):
    cfg = {k: v for k, v in CFG.items() if k != "name"}
    env.stage = "staged"
    assert go(env, pve=Pve(cfg), macos="sequoia")[0].verified


def test_versions_lists_and_wraps_errors(monkeypatch):
    monkeypatch.setattr(engine, "list_releases", lambda get: [{"tag": "v2.9"}])
    assert nf.versions() == [{"tag": "v2.9"}]

    def boom(get):
        raise engine.ReleaseError("offline")
    monkeypatch.setattr(engine, "list_releases", boom)
    with pytest.raises(nf.FinishError, match="offline"):
        nf.versions()


def test_a_failing_qm_set_and_a_vga_change_while_staging(env):
    class Refusing(Pve):
        def qm(self, *a):
            if a[0] == "set" and a[2] == "--sata1":
                return CommandResult(False, 1, "bad slot")
            return super().qm(*a)
    pve = Refusing()
    with pytest.raises(nf.FinishError, match="qm set --sata1 failed: bad slot"):
        go(env, pve=pve)
    assert not any(k.startswith("sata") for k in pve.cfg)
    pve = Pve({**CFG, "vga": "virtio"})
    assert go(env, pve=pve)[0].verified and ("set", "9", "--vga", "std") in pve.calls
    assert pve.cfg["vga"] == "std"  # the emulated display is what recovery needs; the card's own display comes with the attach


def test_the_first_boot_start_failing_is_reported(env):
    class SecondStartFails(Pve):
        starts = 0

        def qm(self, *a):
            if a[0] == "start":
                SecondStartFails.starts += 1
                if SecondStartFails.starts == 2:
                    return CommandResult(False, 1, "no memory")
            return super().qm(*a)
    with pytest.raises(nf.FinishError, match="Could not start VM 9: no memory"):
        go(env, pve=SecondStartFails())


def test_a_restart_that_reached_verification_is_never_kicked(env):
    env.guest = deque(["installed", "verifying"] + ["verifying"] * 40 + [VERIFIED])
    res, pve = go(env)
    assert res.verified and not any("restart hung" in e for e in env.events)
    assert ("stop", "9") not in pve.calls[: [c[:2] for c in pve.calls].index(("start", "9"))] and not any(
        "restart hung" in e for e in env.events)


def test_a_failure_after_staging_stops_the_vm_and_drops_the_payload(env):
    env.attach_ok = False
    pve = Pve()
    with pytest.raises(nf.FinishError, match="Attaching the card failed"):
        go(env, pve=pve)
    assert not any(k.startswith("sata") for k in pve.cfg) and not (env.tmp / "nullmoth-payload-9.img").exists()

    class Interrupted(Pve):
        def qm(self, *a):
            if a[0] == "start" and sum(1 for c in self.calls if c[:2] == ("start", "9")) >= 1:
                self.status = "running"
                raise KeyboardInterrupt
            return super().qm(*a)
    env.attach_ok = True
    pve = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        go(env, pve=pve)
    assert pve.status == "stopped" and not any(k.startswith("sata") for k in pve.cfg)  # stopped, payload dropped


def test_the_vm_started_during_the_downloads_is_refused(env, monkeypatch):
    pve = Pve()
    seen = []
    real = nf.fetch_release if hasattr(nf, "fetch_release") else None
    assert real is None

    def fetch(pick, d, read, fetch_fn):
        pve.status = "running"  # someone started the VM while the release downloaded
        seen.append(1)
        return env.tarball, "a" * 64
    monkeypatch.setattr(engine, "fetch_release", fetch)
    with pytest.raises(nf.FinishError, match="running"):
        go(env, pve=pve)
    assert seen and not any(c[0] == "set" for c in pve.calls)


def test_the_payload_image_goes_to_a_folder_that_proxmox_does_not_list(env, tmp_path):
    clock = Clock()
    pve = Pve()
    env.pve = pve
    res = nf.finish(9, "0000:01:00.0", "latest", console=Console(), pve=pve, iso_dir=env.tmp, on_event=lambda m: None,
                    clock=clock.monotonic, sleep=clock.sleep, find_ip=lambda m: "", probe=lambda i, p: True)
    assert res.verified and (env.tmp / "nullmoth").is_dir()
