"""igpu_finish: stage the graphics package from recovery, attach the iGPU, first boot, restart."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from osx_proxmox_next import igpu_attach, igpu_finish
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.infrastructure import CommandResult
from osx_proxmox_next.nullmoth import AttachPlan
from osx_proxmox_next.oc_builder import IgpuAssets
from osx_proxmox_next.unattended import UnattendedError

@pytest.fixture(autouse=True)
def accel(monkeypatch):
    """The host-side acceleration check reads the real iGPU registers: scripted here."""
    state = SimpleNamespace(answer=(True, "RCS_TAIL=0x1"), calls=0)

    def check(**kw):
        state.calls += 1
        return state.answer
    monkeypatch.setattr(igpu_finish.igpu_engine, "check_accel", check)
    return state


CFG = {
    "name": "macos-sequoia", "machine": "pc", "vga": "std",
    "args": '-device isa-applesmc,osk="x" -smbios type=2 -machine max-ram-below-4g=2G',
    "ide0": "local-lvm:vm-9-disk-1,media=disk", "virtio0": "local-lvm:vm-9-disk-2,size=64G",
    "efidisk0": "local-lvm:vm-9-disk-0,size=4M",
}


class Pve:
    """A scripted Proxmox: config dict, status, disk import, and a VM that halts after a number of status polls."""

    def __init__(self, cfg=None, status="stopped", halts=(1, 1), import_ok=True, start_ok=True):
        self.cfg, self.status = dict(CFG if cfg is None else cfg), status
        self.halts, self.polls, self.disk = deque(halts), None, 10
        self.import_ok, self.start_ok, self.calls = import_ok, start_ok, []

    def text(self):
        return "\n".join(f"{k}: {v}" for k, v in self.cfg.items())

    def qm(self, *a):
        self.calls.append(a)
        sub = a[0]
        if sub == "status":
            if self.status == "running" and self.polls is not None:
                self.polls -= 1
                if self.polls <= 0:
                    self.status = "stopped"
            return CommandResult(True, 0, f"status: {self.status}")
        if sub == "config":
            return CommandResult(True, 0, self.text())
        if sub == "disk":
            if a[1:3] == ("import", "--help"):
                return CommandResult(True, 0, "")
            if not self.import_ok:
                return CommandResult(False, 1, "no space")
            self.disk += 1
            return CommandResult(True, 0, f"successfully imported disk 'local-lvm:vm-9-disk-{self.disk}'")
        if sub == "set":
            items = list(a[2:])
            while items:
                flag = items.pop(0)
                if flag == "--delete":
                    key = items.pop(0)
                    old = self.cfg.pop(key, None)
                    if old and not key.startswith("unused"):
                        self.cfg[f"unused{len([k for k in self.cfg if k.startswith('unused')])}"] = old.split(",")[0]
                else:
                    self.cfg[flag[2:]] = items.pop(0)
            return CommandResult(True, 0, "")
        if sub == "start":
            if not self.start_ok:
                return CommandResult(False, 1, "boom")
            self.status, self.polls = "running", (self.halts.popleft() if self.halts else None)
            return CommandResult(True, 0, "")
        if sub == "stop":
            self.status = "stopped"
            return CommandResult(True, 0, "")
        return CommandResult(True, 0, "")

    def pvesm(self, *a):
        return CommandResult(True, 0, "/dev/payload")

    def run(self, argv):
        return self.qm(*argv[1:]) if argv[0] == "qm" else CommandResult(True, 0, "")


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, n):
        self.now += n


class Console:
    def __init__(self, restore_error=""):
        self.typed, self.keys, self.restore_error = [], [], restore_error

    def type_text(self, t):
        self.typed.append(t)

    def sendkey(self, k):
        self.keys.append(k)

    def restore_picker_timeout(self):
        return self.restore_error


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = SimpleNamespace(applied=[], results=deque(["staged", "installed"]), attach_ok=True, mark_ok=True, plan_issues=[], terminal=None,
                        watchdog_ok=True)
    for name in ("w.zip", "a.zip", "x.rom", "g.pkg", "rec.img"):
        (tmp_path / name).write_bytes(b"x")
    assets = IgpuAssets({"whateverxe": tmp_path / "w.zip", "amfipass": tmp_path / "a.zip", "rom": tmp_path / "x.rom",
                         "graphics": tmp_path / "g.pkg"})
    assets.picks = {"whateverxe": {"version": "0.9.0", "tag": "0.9.0", "sha256": "9" * 64, "source": "latest"},
                    "graphics": {"version": "0.4.1", "tag": "v0.4.1", "sha256": "4" * 64, "source": "chosen"}}
    e.asked = []
    monkeypatch.setenv("IGPU_STATE_DIR", str(tmp_path / "state"))
    report = SimpleNamespace(ok=True, issues=[], engine={"info": {"rom": "adl-h"}})
    monkeypatch.setattr(igpu_finish, "check_intel_gpu", lambda **_kw: report)
    monkeypatch.setattr(igpu_finish, "ensure_igpu_assets", lambda iso, key, **k: e.asked.append(k) or assets)
    monkeypatch.setattr(igpu_finish.igpu_engine, "build_payload", lambda pkg, out: (True, ""))
    monkeypatch.setattr(igpu_finish.igpu_engine, "read_result", lambda path: (e.results[0] if len(e.results) == 1 else e.results.popleft()) if e.results else "")
    monkeypatch.setattr(igpu_finish, "resolve_recovery_or_installer_path", lambda cfg: tmp_path / "rec.img")
    monkeypatch.setattr(igpu_finish, "create_snapshot", lambda vmid, adapter=None: SimpleNamespace(path=tmp_path / "snap"))

    def plan(*a, **k):
        p = AttachPlan(9)
        p.issues = list(e.plan_issues)
        p.warnings = ["a note"]
        p.steps = []
        return p
    monkeypatch.setattr(igpu_finish, "prepare_attach_igpu", plan)

    def apply(steps, execute=True, adapter=None, **k):
        e.applied.append([s.title for s in steps])
        ok = e.attach_ok or not steps or "Show every" in steps[0].title or "picker" in steps[0].title
        if steps and "Note in OpenCore" in steps[0].title:
            ok = e.mark_ok
        if steps and "power watchdog" in steps[0].title:
            ok = e.watchdog_ok
        return ApplyResult(ok, [StepResult("t", "c", ok, 0, "")] if not ok else [], tmp_path / "log")
    monkeypatch.setattr(igpu_finish, "apply_plan", apply)

    def terminal(console, on_event, clock, sleep):
        if e.terminal:
            raise e.terminal
    monkeypatch.setattr(igpu_finish, "open_recovery_terminal", terminal)
    e.tmp = tmp_path
    return e


def go(env, pve=None, console=None, **kw):
    clock, pve = Clock(), pve or Pve()
    events: list[str] = []
    res = igpu_finish.finish(9, console=console or Console(), pve=pve, iso_dir=env.tmp, on_event=events.append,
                             clock=clock.monotonic, sleep=clock.sleep, work_dir=env.tmp, **kw)
    return res, pve, events


def test_the_whole_finish(env):
    console = Console()
    res, pve, events = go(env, console=console)
    assert res.installed and res.result == "installed" and res.notes == ["a note"]
    assert console.typed == [igpu_finish.igpu_engine.STAGE_COMMAND] and console.keys == ["ret"]
    # staging ran with the emulated display, nothing leftover, and the recovery and payload disks are gone again
    assert not any(k.startswith("sata") for k in pve.cfg) and pve.cfg["boot"] == "order=ide0;virtio0"
    assert "args" in pve.cfg and "max-ram-below-4g" not in pve.cfg["args"]
    assert ("start", "9") == pve.calls[-1]  # the last thing is the restart
    assert [t[0] for t in env.applied if t] == ["Make the OpenCore picker show recovery only and wait for a key",
                                           "Show every volume in the OpenCore picker again",
                                           "Install the power watchdog on the host",
                                           "Note in OpenCore that the graphics package is installed"]
    assert not (env.tmp / "igpu-payload-9.img").exists()
    assert any("First boot" in e for e in events) and "Started the VM again" in events
    assert res.accelerated and res.accel_detail == "RCS_TAIL=0x1" and "uses the iGPU" in events[-1]


def test_an_installed_package_without_gpu_work_is_not_called_accelerated(env, accel):
    accel.answer = (False, "BCS_TAIL=0x0, RCS_TAIL=0x0")
    res, pve, events = go(env)
    assert res.installed and not res.accelerated and res.accel_detail.startswith("BCS_TAIL=0x0")


def test_the_check_is_skipped_when_nothing_was_installed(env, accel):
    env.results = deque(["staged", "not installed: WhateverXe not loaded (try 1 of 3)"])
    res, pve, events = go(env)
    assert not res.installed and not res.accelerated and accel.calls == 0


def test_a_failed_mark_is_reported(env):
    env.mark_ok = False
    res, _pve, _ = go(env)
    assert res.installed and any("gpu-setup --undo would remove WhateverXe" in n for n in res.notes)


def test_the_picker_timeout_failure_is_reported_not_fatal(env):
    res, _pve, _ = go(env, console=Console(restore_error="no mount"))
    assert res.installed and any("no mount" in n for n in res.notes)


def test_not_installed_is_reported_and_the_vm_still_restarts(env):
    env.results = deque(["staged", "not installed: WhateverXe not loaded (try 1 of 3)"])
    res, pve, events = go(env)
    assert not res.installed and "not loaded" in res.result and pve.calls[-1] == ("start", "9")
    assert "not installed" in events[-1]


def test_a_guest_that_never_powers_off_is_stopped(env):
    res, pve, _ = go(env, pve=Pve(halts=(1, 10**6)))
    assert any("did not power off" in n for n in res.notes) and ("stop", "9") in pve.calls


def test_tahoe_ends_the_wait_when_the_guest_has_left_its_result(env):
    cfg = {**CFG, "name": "macos-tahoe"}
    res, pve, _ = go(env, pve=Pve(cfg, halts=(1, 10**6)))
    assert res.installed and igpu_attach.TAHOE_NOTE in res.notes and ("stop", "9") in pve.calls


def test_tahoe_without_a_result_waits_the_full_time_and_ignores_half_written_text(env, monkeypatch):
    cfg = {**CFG, "name": "macos-tahoe"}
    seen = deque(["staged", "inst", "", "installed"])
    monkeypatch.setattr(igpu_finish.igpu_engine, "read_result", lambda path: seen.popleft() if seen else "installed")
    res, pve, _ = go(env, pve=Pve(cfg, halts=(1, 10**6)))
    assert res.installed  # "inst" and "" were not trusted; the final read is the real one


def test_the_release_can_be_given(env):
    cfg = {k: v for k, v in CFG.items() if k != "name"}
    assert go(env, pve=Pve(cfg), macos="ventura")[0].installed
    with pytest.raises(igpu_finish.FinishError, match="which macOS"):
        go(env, pve=Pve(cfg))


@pytest.mark.parametrize("change,text", [
    ({"machine": "q35"}, "i440fx"),
    ({"sata1": "x:vm-9-disk-5,media=disk"}, "recovery disk"),
    ({"hostpci0": "0000:06:00,pcie=1"}, "hostpci0"),
    ({"args": "-device other"}, "not a macOS VM"),
])
def test_vm_checks(env, change, text):
    with pytest.raises(igpu_finish.FinishError, match=text):
        go(env, pve=Pve({**CFG, **change}))


def test_a_running_vm_and_a_missing_vm_are_refused(env):
    with pytest.raises(igpu_finish.FinishError, match="running"):
        go(env, pve=Pve(status="running"))

    class Gone(Pve):
        def qm(self, *a):
            return CommandResult(False, 2, "no such VM") if a[0] == "status" else super().qm(*a)
    with pytest.raises(igpu_finish.FinishError, match="not found"):
        go(env, pve=Gone())


def test_an_unready_host_or_plan_stops_before_anything_changes(env, monkeypatch):
    monkeypatch.setattr(igpu_finish, "check_intel_gpu", lambda **_kw: SimpleNamespace(ok=False, issues=["driver: i915"], engine={}))
    pve = Pve()
    with pytest.raises(igpu_finish.FinishError, match="not ready"):
        go(env, pve=pve)
    assert not any(c[0] == "set" for c in pve.calls)


def test_a_refused_attach_plan_stops_before_staging(env):
    env.plan_issues = ["VM is running"]
    pve = Pve()
    with pytest.raises(igpu_finish.FinishError, match="Cannot attach"):
        go(env, pve=pve)
    assert not any(c[0] == "start" for c in pve.calls)


def test_payload_build_and_import_failures(env, monkeypatch):
    monkeypatch.setattr(igpu_finish.igpu_engine, "build_payload", lambda pkg, out: (False, "mkfs failed"))
    with pytest.raises(igpu_finish.FinishError, match="mkfs failed"):
        go(env)
    monkeypatch.undo()


def test_import_failure_is_a_finish_error(env):
    with pytest.raises(igpu_finish.FinishError, match="Could not import"):
        go(env, pve=Pve(import_ok=False))


def test_staging_failures_clean_up_and_say_why(env):
    env.results = deque(["failed: the installed macOS data volume is not mounted"])
    pve = Pve()
    with pytest.raises(igpu_finish.FinishError, match="FileVault"):
        go(env, pve=pve)
    assert not any(k.startswith("sata") for k in pve.cfg)
    assert pve.cfg["vga"] == "std" and "boot" not in pve.cfg and pve.cfg["args"] == CFG["args"]  # put back as it was
    assert env.applied[-1] == ["Show every volume in the OpenCore picker again"]  # best effort, even on failure
    assert not (env.tmp / "igpu-payload-9.img").exists()


def reads(monkeypatch, seq, rest="installed"):
    items = iter(seq)
    monkeypatch.setattr(igpu_finish.igpu_engine, "read_result", lambda path: next(items, rest))


def test_staging_that_never_halts_is_stopped_and_refused(env, monkeypatch):
    reads(monkeypatch, [], rest="started")  # the script began but never reported a result and never halted
    with pytest.raises(igpu_finish.FinishError, match="did not finish"):
        go(env, pve=Pve(halts=(10**6,)))


def test_staging_whose_halt_wedges_is_stopped_by_the_host_once_the_result_is_in(env, monkeypatch):
    """Tahoe recovery: stage.sh wrote "staged" and synced, then its halt wedged (every vCPU spins, the VM never stops)."""
    reads(monkeypatch, ["started", "staged", "staged", "installed"])
    clock = Clock()
    pve = Pve(halts=(10**6, 1))
    res = igpu_finish.finish(9, console=Console(), pve=pve, iso_dir=env.tmp, on_event=lambda m: None, clock=clock.monotonic,
                             sleep=clock.sleep, work_dir=env.tmp, macos="tahoe")
    assert res.installed
    assert ("stop", "9") in pve.calls  # the host stopped the wedged recovery itself
    assert clock.now < igpu_finish.STAGE_TIMEOUT  # and did not sit out the 15 minute timeout


def test_a_staging_failure_reported_by_a_wedged_halt_is_still_reported(env, monkeypatch):
    reads(monkeypatch, ["started", "failed: cannot copy the package", "failed: cannot copy the package"])
    with pytest.raises(igpu_finish.FinishError, match="Staging failed: failed: cannot copy"):
        go(env, pve=Pve(halts=(10**6,)))


def test_a_dropped_key_retypes_the_command_until_the_script_starts(env, monkeypatch):
    reads(monkeypatch, [""] * 13 + ["started", "staged", "staged", "installed"])
    console = Console()
    res, _pve, events = go(env, pve=Pve(halts=(40, 1)), console=console)
    assert res.installed and console.typed == [igpu_finish.igpu_engine.STAGE_COMMAND] * 2
    assert console.keys == ["ret", "ctrl-c", "ret"]
    assert any("typing it again" in e for e in events)


def test_a_command_that_never_starts_is_refused_after_three_tries(env, monkeypatch):
    reads(monkeypatch, [""] * 1000)
    console = Console()
    with pytest.raises(igpu_finish.FinishError, match="did not start after 3 tries"):
        go(env, pve=Pve(halts=(10**6,)), console=console)
    assert len(console.typed) == 3 and console.keys == ["ret", "ctrl-c", "ret", "ctrl-c", "ret"]


def test_the_start_wait_includes_the_time_the_typing_took(env, monkeypatch):
    clock = Clock()
    waits: list[float] = []
    monkeypatch.setattr(igpu_finish, "_staging_started", lambda pve, vmid, ref, wait, c, s: waits.append(wait) or True)

    class Slow(Console):
        def type_text(self, t):
            clock.sleep(len(t) * 1.5)
            super().type_text(t)
    igpu_finish.finish(9, console=Slow(), pve=Pve(), iso_dir=env.tmp, on_event=lambda m: None, clock=clock.monotonic,
                       sleep=clock.sleep, work_dir=env.tmp)
    assert waits == [igpu_finish.START_WAIT + len(igpu_finish.igpu_engine.STAGE_COMMAND) * 1.5]


def test_a_tahoe_staging_failure_says_to_run_again_and_puts_the_vm_back(env, monkeypatch):
    reads(monkeypatch, [], rest="started")
    pve = Pve(halts=(10**6,))
    events: list[str] = []
    clock = Clock()
    with pytest.raises(igpu_finish.FinishError) as err:
        igpu_finish.finish(9, console=Console(), pve=pve, iso_dir=env.tmp, on_event=events.append, clock=clock.monotonic,
                           sleep=clock.sleep, work_dir=env.tmp, macos="tahoe")
    assert igpu_attach.TAHOE_STAGE_NOTE in str(err.value) and "did not finish" in str(err.value)
    # Tahoe is not announced as unsupported up front: the staging works there, so only a failure says anything
    assert not any("does not work on macOS Tahoe" in e for e in events) and "run the same command again" in str(err.value)
    # nothing the staging added is left behind
    assert not any(k.startswith("sata") for k in pve.cfg) and pve.cfg["vga"] == CFG["vga"] and pve.cfg["args"] == CFG["args"]
    assert pve.status == "stopped"


def test_finish_refuses_a_vm_that_already_has_the_igpu_so_the_package_is_never_staged_twice(env):
    cfg = {**CFG, "hostpci0": "0000:00:02.0,legacy-igd=1"}
    console = Console()
    with pytest.raises(igpu_finish.FinishError, match="already has a PCI device on hostpci0"):
        go(env, pve=Pve(cfg), console=console)
    assert console.typed == []


def test_the_first_boot_script_is_one_shot():
    from pathlib import Path
    text = (Path(igpu_finish.__file__).parent / "data/gpu/igpu_firstboot.sh").read_text()
    ok = text.index('if /usr/sbin/installer -pkg "$PKG" -target /')
    # a successful install removes the package, the script, its LaunchDaemon and the try counter before reporting
    assert 'rm -f "$PKG" "$PLIST" "$BASE/firstboot.sh" "$TRIES_FILE"' in text[ok:text.index('finish "installed"')]
    # after the last try the LaunchDaemon goes too, so nothing runs on later boots
    start = text.index('if [ "$tries" -ge "$MAX_TRIES" ]')
    assert 'rm -f "$PLIST"' in text[start:text.index("\nfi\n", start)]


def test_recovery_that_never_shows_is_a_finish_error(env):
    env.terminal = UnattendedError("recovery never started")
    with pytest.raises(igpu_finish.FinishError, match="recovery never started"):
        go(env)


def test_start_failures(env):
    with pytest.raises(igpu_finish.FinishError, match="Could not start"):
        go(env, pve=Pve(start_ok=False))


def test_an_attach_that_fails_names_the_step_and_the_undo(env):
    env.attach_ok = False
    # the stage step itself must pass, so only the attach plan (which has steps here) fails
    def plan(*a, **k):
        p = AttachPlan(9)
        p.steps = [SimpleNamespace(title="Attach the iGPU")]
        return p
    igpu_finish.prepare_attach_igpu = plan
    with pytest.raises(igpu_finish.FinishError, match="gpu-setup --undo"):
        go(env)


def test_first_start_failure_after_attach(env):
    class OneStart(Pve):
        def qm(self, *a):
            if a[0] == "start" and len([c for c in self.calls if c[0] == "start"]) >= 1:
                self.calls.append(a)
                return CommandResult(False, 1, "boom")
            return super().qm(*a)
    with pytest.raises(igpu_finish.FinishError, match="Could not start"):
        go(env, pve=OneStart())


def test_helpers(env):
    with pytest.raises(igpu_finish.FinishError):
        igpu_finish._storage_of({})
    assert igpu_finish._storage_of({"ide0": "dir:vm-1,media=disk"}) == "dir"
    pve = Pve({**CFG, "sata1": "local-lvm:vm-9-disk-5,media=disk"})
    igpu_finish._drop_disk(pve, 9, "sata1")
    assert "sata1" not in pve.cfg and not any(v == "local-lvm:vm-9-disk-5" for v in pve.cfg.values())
    igpu_finish._drop_disk(pve, 9, "sata2")  # nothing there: no error
    assert igpu_finish._read_payload_result(Pve(), "ref") == "staged"  # the fake read_result answers


def test_tahoe_gets_the_power_watchdog_right_after_the_attach(env):
    res, pve, events = go(env, pve=Pve({**CFG, "name": "macos-tahoe"}, halts=(1, 10**6)))
    titles = [t[0] for t in env.applied if t]
    assert res.watchdog and titles.index("Install the power watchdog on the host") == len(titles) - 2  # before "mark"
    assert any("Installed the watchdog" in ev for ev in events)
    watchdog = next(t for t in env.applied if t and t[0] == "Install the power watchdog on the host")
    assert "Give the VM a serial port (socket) for the kernel console" in watchdog  # the test VM has no serial0


def test_a_failed_watchdog_install_is_a_note_not_a_failure(env):
    env.watchdog_ok = False
    res, _pve, _ = go(env, pve=Pve({**CFG, "name": "macos-tahoe"}, halts=(1, 10**6)))
    assert res.installed and not res.watchdog
    assert any("osx-next-cli igpu-watchdog 9" in n for n in res.notes)


def test_every_release_gets_the_watchdog_that_records_crashes(env):
    res, _pve, _ = go(env)  # the default test VM is not Tahoe
    assert res.watchdog and any(t and "power watchdog" in t[0] for t in env.applied)


def test_the_chip_is_checked_against_the_release_that_gets_installed(env, monkeypatch):
    seen = []
    report = SimpleNamespace(ok=True, issues=[], engine={"info": {"rom": "adl-h"}})
    monkeypatch.setattr(igpu_finish, "check_intel_gpu", lambda **kw: seen.append(kw) or report)
    plans = []
    real = igpu_finish.prepare_attach_igpu
    monkeypatch.setattr(igpu_finish, "prepare_attach_igpu", lambda *a, **kw: plans.append(kw["wex"]) or real(*a, **kw))
    go(env, wex="0.8.0")
    assert seen == [{"wex": "0.8.0"}] and plans == ["0.8.0", "0.8.0"]


def test_the_chosen_releases_reach_the_download_and_the_state_file(env):
    res, _pve, events = go(env, wex="0.8.0", graphics="latest")
    assert env.asked == [{"wex": "0.8.0", "graphics": "latest"}]
    assert any("Using WhateverXe 0.9.0 and WhateverXe Graphics 0.4.1" in e for e in events)
    state = json.loads((env.tmp / "state/9.json").read_text())
    assert state["whateverxe_version"] == "0.9.0" and state["whateverxe_source"] == "latest"
    assert state["graphics_source"] == "chosen" and state["package_installed"] is True and state["macos"] == "sequoia"
    assert res.versions[0] == "WhateverXe 0.9.0 (latest when installed)"


def test_an_unreachable_github_is_a_finish_error_with_the_advice(env, monkeypatch):
    def boom(iso, key, **k):
        raise igpu_finish.igpu_engine.ReleaseError("Could not reach GitHub (x). Check the network and run the command again.")
    monkeypatch.setattr(igpu_finish, "ensure_igpu_assets", boom)
    with pytest.raises(igpu_finish.FinishError, match="Could not reach GitHub"):
        go(env)


def test_an_unwritable_state_file_is_a_note(env, monkeypatch):
    monkeypatch.setenv("IGPU_STATE_DIR", "/proc/nope/state")
    res, _pve, _ = go(env)
    assert res.installed and any("Could not record the installed versions" in n for n in res.notes)


def test_choose_release_lists_newest_first_and_returns_latest_or_a_tag():
    said: list[str] = []
    ask = iter(["", "2", "0.9.0", "v0.4.1", "x", "9", "", ])
    assert igpu_finish.choose_release("whateverxe", lambda p: next(ask), said.append) == "latest"
    assert said[0].startswith("WhateverXe releases") and "1. 0.9.0" in said[1] and "(latest)" in said[1]
    assert igpu_finish.choose_release("whateverxe", lambda p: next(ask), said.append) == "0.8.0"
    assert igpu_finish.choose_release("whateverxe", lambda p: next(ask), said.append) == "latest"  # the latest tag typed
    assert igpu_finish.choose_release("graphics", lambda p: next(ask), said.append) == "latest"
    assert igpu_finish.choose_release("whateverxe", lambda p: next(ask), said.append) == "latest"  # "x" is refused, "9" too, then Enter
    assert sum("is not in the list" in m for m in said) == 2


def test_choose_release_gives_up_after_five_wrong_answers_and_handles_end_of_input():
    with pytest.raises(igpu_finish.igpu_engine.ReleaseError, match="No WhateverXe release was picked"):
        igpu_finish.choose_release("whateverxe", lambda p: "nope", lambda m: None)

    def eof(prompt):
        raise EOFError
    assert igpu_finish.choose_release("whateverxe", eof, lambda m: None) == "latest"


def test_the_attach_is_checked_against_the_github_digest_and_the_amfipass_pin(env, monkeypatch):
    seen = []

    def plan(*a, **k):
        seen.append(k)
        p = AttachPlan(9)
        p.steps = []
        return p
    monkeypatch.setattr(igpu_finish, "prepare_attach_igpu", plan)
    go(env)
    assert seen and all(k["wex_sha"] == "9" * 64 and k["amfi_sha"] == igpu_finish.igpu_engine.PINS["amfipass"][2] for k in seen)
