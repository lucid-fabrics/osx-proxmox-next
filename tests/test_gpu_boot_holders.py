"""Another VM that starts with the host takes the card: the engine side (holders, the warning, the approval text),
the plain-words plan view and the undo wording."""
from __future__ import annotations

import dataclasses
from dataclasses import replace
from types import SimpleNamespace

from gpu_fakes import add_card, write
from nullmoth_fakes import FakeQm
from osx_proxmox_next import amd_host, gpu, gpu_attach, host_setup, nullmoth
from osx_proxmox_next.gpu_flow import Choice, Preview, build_before, build_preview, changes_text
from osx_proxmox_next.gpu_plan_view import step_summary
from test_gpu_setup import SEQUOIA, Env, ready

CARD = "hostpci0: 0000:06:00,pcie=1\n"
ONE = ("VM 103 starts with this host and has this card. After the restart it takes the card before the Mac can. "
       "Shut it down then, or turn off its Start at boot option.")
SEVERAL = ("VMs 103, 106 start with this host and have this card. After the restart one of them takes the card "
           "before the Mac can. Shut it down then, or turn off their Start at boot option.")


class NamedQm(FakeQm):
    """qm list with real VM names: the screens say "chimeraos (VM 103)"."""

    names = {103: "chimeraos", 106: "bazzite"}

    def run(self, argv):
        res = super().run(argv)
        if argv[:2] == ["qm", "list"]:
            rows = [f"{v} {self.names.get(v, 'vm' + str(v))} {st} 4096 64 1" for v, (st, _c) in self.vms.items()]
            return type(res)(True, 0, "VMID NAME STATUS MEM BOOTDISK PID\n" + "\n".join(rows))
        return res


def host(tmp_path, vms):
    env = Env(tmp_path)
    env.qm = NamedQm({100: ("stopped", SEQUOIA), **vms})
    add_card(env.root, driver="amdgpu")
    return env


def card_of(s):
    return next(c for c in s.scan() if c.bdf == "0000:06:00")


# ── who holds the card ───────────────────────────────────────────────

def test_holders_carry_their_name_and_whether_they_start_with_the_host(tmp_path):
    env = host(tmp_path, {103: ("stopped", CARD + "onboot: 1\n"), 106: ("running", CARD + "onboot: 0\n"),
                          107: ("stopped", CARD + "onboot: 1\ntemplate: 1\n"), 108: ("stopped", "cores: 2\n")})
    found = gpu.gpu_holders(env.qm, "0000:06:00", 100)
    assert [(h.vmid, h.name, h.running, h.onboot) for h in found] == [
        (103, "chimeraos", False, True), (106, "bazzite", True, False), (107, "vm107", False, False)]
    assert gpu.gpu_users(env.qm, "0000:06:00", 100) == [(103, False), (106, True), (107, False)]
    assert gpu.gpu_holders(env.qm, "0000:06:00", 103)[0].vmid == 106       # the VM asked about is left out


def test_the_starts_with_the_host_warning_is_on_the_card_so_every_caller_gets_it(tmp_path):
    env = host(tmp_path, {103: ("stopped", CARD + "onboot: 1\n")})
    card = card_of(env.session())
    assert card.starters == [103] and card.selectable
    assert card.notes[:2] == [gpu.shared_note([103]), ONE]                   # the old warning stays
    assert gpu.starts_note([103]) == ONE
    env = host(tmp_path / "two", {103: ("stopped", CARD + "onboot: 1\n"), 106: ("stopped", CARD + "onboot: 1\n"),
                                  108: ("stopped", CARD)})
    card = card_of(env.session())
    assert card.starters == [103, 106] and SEVERAL in card.notes and card.share_notes[-1] == SEVERAL
    quiet = card_of(host(tmp_path / "quiet", {108: ("stopped", CARD)}).session())
    assert quiet.starters == [] and not any("starts with this host" in n for n in quiet.notes)


def test_the_warning_reaches_before_you_continue_once(tmp_path):
    env = host(tmp_path, {103: ("stopped", CARD + "onboot: 1\n")})
    s = env.session()
    prev = build_preview(s, Choice(card_of(s), 100, "gpu"))
    warnings = build_before(prev).warnings
    assert "Warning: " + ONE in warnings and "Warning: " + gpu.shared_note([103]) in warnings
    assert len(warnings) == len(set(warnings))                               # the attach plan repeats it: shown once
    later = build_before(build_preview(s, Choice(card_of(s), None, "gpu"))).warnings
    assert "Warning: " + ONE in later                                        # also when the Mac is decided later


def test_the_session_names_the_vms_that_hold_the_card_after_the_restart(tmp_path):
    env = host(tmp_path, {103: ("running", CARD + "onboot: 1\n")})
    s = env.session()
    s.state.update(gpu={"bdf": "0000:06:00"}, vm=100)
    assert [(h.vmid, h.name, h.running, h.onboot) for h in s.card_holders()] == [(103, "chimeraos", True, True)]
    s.state["gpu"] = None
    assert s.card_holders() == []


def test_a_running_holder_fails_the_free_check_with_who_it_is(tmp_path):
    env = host(tmp_path, {103: ("running", CARD + "onboot: 1\n"), 106: ("stopped", CARD)})
    s = env.session()
    s.state.update(gpu={"bdf": "0000:06:00", "vendor": "amd", "device": 0x73FF, "boot_vga": False,
                        "name": "x", "identity": None}, vm=100)
    busy = [h for h in s.card_holders() if h.running]
    assert [h.vmid for h in busy] == [103]


def test_the_free_check_fails_while_a_vm_runs_with_the_card_and_passes_when_it_stops(tmp_path):
    env = Env(tmp_path, cpu="intel")
    env.qm = NamedQm({100: ("stopped", SEQUOIA)})
    add_card(env.root)                                                       # bound to vfio-pci: the host is ready
    ready(env)
    s = env.session()
    assert s.choose("06:00", 100, "gpu") == []
    env.qm.vms.update({103: ("running", CARD + "onboot: 1\n"), 106: ("stopped", CARD)})
    checks = s.verify()
    busy = checks[-1]
    assert busy["status"] == "fail" and busy["fix"] == "free" and busy["label"] == "Card is in use by VM 103"
    assert busy["holders"] == [(103, "chimeraos", True, True)] and busy["stopped"] == [106]
    assert 6 not in s.state["done"]                                          # a check that did not pass is not done
    env.qm.vms[103] = ("stopped", CARD + "onboot: 1\n")
    assert not [c for c in s.verify() if c["status"] == "fail"] and 6 in s.state["done"]


def test_the_command_line_warns_about_a_vm_that_starts_with_the_host(tmp_path):
    from test_gpu_setup_cli import go
    env = host(tmp_path, {103: ("stopped", CARD + "onboot: 1\n")})
    code, out = go(env, "--gpu", "0000:06:00", "--vm", "100", "--display", "gpu")
    assert "WARN: " + gpu.shared_note([103]) in out and "WARN: " + ONE in out and "Nothing was changed" in out


# ── every warning and refusal of the engine is shown or blocks ───────

# lists of work to do, and "skipped" (things already right: OK lines, no reason to warn), not warnings
NOT_TEXT = {"changes", "commands", "refresh", "steps", "skipped"}


def engine_texts():
    """One marker for every list of words the engine's plan types have, found from the types themselves: a new
    warning source added later has a list here and so a marker the dialogs must show (or the test fails)."""
    plan = host_setup.new_plan()
    host_keys = [k for k, v in plan.items() if isinstance(v, list) and k not in NOT_TEXT]
    vm_keys = [f.name for f in dataclasses.fields(nullmoth.AttachPlan)
               if f.default_factory is list and f.name not in NOT_TEXT]      # type: ignore[misc]
    return host_keys, vm_keys


def full_preview(card, vm_issues=(), refusals=()):
    host_keys, vm_keys = engine_texts()
    plan = host_setup.new_plan()
    for key in host_keys:
        plan[key] = [f"host-{key}"]
    plan["refusals"] = list(refusals)
    vm_plan = nullmoth.AttachPlan(100)
    for key in vm_keys:
        setattr(vm_plan, key, [f"vm-{key}"])
    vm_plan.issues = list(vm_issues)
    return Preview(card, 100, "gpu", plan=plan, vm_plan=vm_plan, typed_needed=True, typed_extra=["Careful: only-ssh"])


def test_every_warning_the_engine_returns_is_in_before_you_continue(tmp_path):
    card = card_of(host(tmp_path, {103: ("stopped", CARD + "onboot: 1\n")}).session())
    prev = full_preview(card)
    before = build_before(prev)
    shown = "\n".join(before.warnings + before.notes)
    host_keys, vm_keys = engine_texts()
    # a refusal stops the button instead of being listed here, so it is checked by the test below
    wanted = [f"host-{k}" for k in host_keys if k != "refusals"] + [f"vm-{k}" for k in vm_keys if k != "issues"]
    for text in wanted + ["only-ssh", ONE, gpu.shared_note([103])]:
        assert text in shown, text
    assert {"warnings", "hard_warnings", "notes", "manual"} <= set(host_keys) and "hints" in vm_keys
    assert before.phrase and not before.empty                                 # the phrase is asked in the same dialog
    assert build_before(full_preview(replace(card, vendor="nvidia"))).nvidia     # the NullMoth notice and its phrase
    assert any("Intel iGPU" in n for n in build_before(full_preview(replace(card, vendor="intel"))).notes)


def test_every_refusal_the_engine_returns_blocks_the_flow(tmp_path):
    card = card_of(host(tmp_path, {}).session())
    assert full_preview(card, vm_issues=["vm refused"]).refusals == ["vm refused"]
    assert full_preview(card, refusals=["host refused"]).refusals == ["host refused"]
    only_display = full_preview(card, refusals=["The card is the only display adapter"])
    assert only_display.refusals == [] and build_before(only_display).phrase    # typed away, never silently


def test_a_card_another_vm_lists_is_refused_unless_the_dialog_showed_that_vm(tmp_path):
    env = host(tmp_path, {103: ("stopped", CARD)})
    s = env.session()
    assert "Confirm that you want to use it anyway" in s.choose("06:00", 100, "gpu")[0]
    assert s.choose("06:00", 100, "gpu", share_ok=True, confirmed=[]) != []     # a holder the dialog never listed
    assert s.choose("06:00", 100, "gpu", share_ok=True, confirmed=[103]) == []


# ── the undo wording ─────────────────────────────────────────────────

def changed_host(tmp_path):
    env = Env(tmp_path)
    add_card(env.root)
    write(env.root, "/etc/old.conf", "before\n")
    s = env.session()
    state = s.state
    for path, existed in (("/etc/old.conf", True), ("/etc/new.conf", False)):
        backup = tmp_path / ("bak" + path.replace("/", "_"))
        backup.write_text("before\n")
        write(env.root, path, "after\n")
        state["host_changes"].append({"path": path, "existed": existed, "backup": str(backup if existed else ""),
                                      "stamp": "s", "sha_before": "", "sha_after": host_setup._sha("after\n"),
                                      "reverted": False})
    s.save()
    return env, s


def test_undo_says_removed_for_a_file_the_setup_created_and_restored_for_one_it_changed(tmp_path):
    env, s = changed_host(tmp_path)
    assert s.undo_plan() == ["Remove /etc/new.conf", "Restore /etc/old.conf", "The host needs one restart afterwards."]
    ok, messages = s.undo()
    assert ok and messages[:2] == ["Removed /etc/new.conf", "Restored /etc/old.conf"]
    assert messages[-1] == "Restart this host when it suits you. The boot change stays active until then."
    assert "Reboot" not in "\n".join(messages)


def test_the_undo_plan_names_the_mac_that_has_the_card(tmp_path):
    env = Env(tmp_path)
    env.qm = NamedQm({105: ("stopped", SEQUOIA)})
    s = env.session()
    s.macos_vms = lambda: ["VMID NAME STATUS", "105 macos-sequoia stopped"]
    gpu.record_path(105).parent.mkdir(parents=True, exist_ok=True)
    gpu.record_path(105).write_text('{"vmid": 105, "bdf": "0000:06:00", "vendor": "amd"}')
    s.state["vm_records"] = [105]
    assert s.undo_plan() == ["Take the card off macos-sequoia (VM 105)"]       # no files: no restart line


# ── "See every change" in plain words ────────────────────────────────

def attach_steps(stop=True):
    rec = gpu.new_record(105, "0000:03:00", "amd", {"args": "-device isa-applesmc"}, amd_host.AMD_PROFILE,
                         "0000:03:00,pcie=1", vga_none=True)
    return gpu_attach.build_attach_steps(105, "0000:03:00", "-device isa-applesmc", 0x73BF, True, rec, stop_first=stop)


def test_every_vm_step_has_one_plain_line_with_its_real_values():
    lines = [step_summary(s) for s in attach_steps()]
    assert lines[0] == "Shuts down VM 105 so the card can be attached"
    assert lines[1] == "Checks VM 105 is stopped before touching it"
    assert lines[2].startswith("Saves the current settings of VM 105 so undo can put them back")
    assert lines[3] == "Edits OpenCore's config.plist on the Mac's boot disk: adds boot argument agdpmod=pikera"
    assert lines[4] == "Hands the card (0000:03:00,pcie=1) to VM 105 as hostpci0"
    assert lines[6].startswith("Turns the emulated display off on VM 105")
    assert all("bash" not in line and "'" not in line.replace("OpenCore's", "").replace("Mac's", "") for line in lines)


def test_the_plain_lines_cover_the_other_step_shapes():
    from osx_proxmox_next.domain import PlanStep
    assert step_summary(PlanStep("x", ["qm", "set", "9", "--delete", "hostpci0"])) == "Removes hostpci0 from VM 9"
    assert step_summary(PlanStep("x", ["qm", "set", "9", "--machine", "pc"])) == "Sets the machine type of VM 9 to pc"
    assert step_summary(PlanStep("x", ["qm", "set", "9", "--cores", "4"])) == "Changes cores of VM 9"
    assert step_summary(PlanStep("x", ["rm", "-f", "/var/lib/x.json"])).startswith("Deletes the saved settings file")
    assert step_summary(PlanStep("x", ["install", "-m", "644", "a.rom", "/usr/share/kvm/a.rom"])).endswith("/usr/share/kvm/a.rom")
    assert step_summary(PlanStep("x", ["true"])).startswith("Runs one command. Press x")
    restore = [s for s in gpu_attach.build_detach_steps(
        105, "-device x", True, gpu.new_record(105, "0000:03:00", "amd", {}, amd_host.AMD_PROFILE, "h", oc="gpu"))
        if s.argv[0] == "bash" and "$OC_MNT" in s.argv[2]]
    assert step_summary(restore[0]) == "Puts the Mac's OpenCore config.plist back as it was before the card"


def test_changes_text_is_plain_by_default_and_exact_on_request(tmp_path):
    plan = host_setup.new_plan()
    vm_plan = SimpleNamespace(steps=attach_steps(), issues=[], hints=[], warnings=[])
    plain = changes_text(plan, vm_plan, 105)
    exact = changes_text(plan, vm_plan, 105, exact=True)
    assert "adds boot argument agdpmod=pikera" in plain and "'\"'\"'" not in plain and "GPU_OC_PY" not in plain
    assert len(plain.splitlines()) < 30 < len(exact.splitlines())
    assert "qm set 105 --hostpci0 0000:03:00,pcie=1" in exact and "GPU_OC_PY" in exact


def test_the_not_offered_reason_is_a_few_words(tmp_path):
    env = host(tmp_path, {})
    add_card(env.root, bdf="0000:41:00", device=0x73C0, group=9, driver="amdgpu")
    cards = {c.bdf: c for c in env.session().scan()}
    assert cards["0000:41:00"].short_why_not == "no Apple driver" and cards["0000:06:00"].short_why_not == ""
    env.qm.vms[120] = ("running", CARD)
    assert {c.bdf: c for c in env.session().scan()}["0000:06:00"].short_why_not == "in use by VM 120"


def test_a_snapshot_section_is_never_read_as_the_live_config():
    raw = "onboot: 0\nhostpci0: 0000:06:00,pcie=1\n\n[before-upgrade]\nonboot: 1\nhostpci1: 0000:07:00\nsnaptime: 5\n"
    cfg = gpu.parse_config(raw)
    assert cfg == {"onboot": "0", "hostpci0": "0000:06:00,pcie=1"}


FORMS = ["0000:06:00,pcie=1", "06:00,pcie=1", "0000:06:00.0,pcie=1,x-vga=1", "06:00.1", "host=0000:06:00.0,pcie=1",
         "0000:06:00.0;0000:06:00.1,pcie=1", "06:00.0;06:00.1"]


def test_a_vm_holds_the_card_in_every_form_a_hostpci_value_can_name_it(tmp_path):
    for n, form in enumerate(FORMS):
        env = host(tmp_path / str(n), {106: ("stopped", f"hostpci0: {form}\nonboot: 1\n")})
        found = gpu.gpu_holders(env.qm, "0000:06:00", 100)
        assert [(h.vmid, h.onboot) for h in found] == [(106, True)], form
        assert gpu.gpu_users(env.qm, "0000:06:00", 100) == [(106, False)], form
    other = host(tmp_path / "other", {106: ("stopped", "hostpci0: 0000:07:00.0,pcie=1\nonboot: 1\n")})
    assert gpu.gpu_holders(other.qm, "0000:06:00", 100) == []


def test_a_resource_mapping_holds_the_card_and_a_snapshot_never_hides_the_boot_flag(tmp_path):
    assert gpu.hostpci_bdfs({"hostpci0": "mapping=gpu0"}, {"gpu0": {"0000:06:00"}}) == {"hostpci0": {"0000:06:00"}}
    env = host(tmp_path / "b", {106: ("stopped", "hostpci0: 0000:06:00.0,pcie=1,x-vga=1\nonboot: 1\n\n[snap1]\nonboot: 0\n")})
    assert [(h.vmid, h.onboot) for h in gpu.gpu_holders(env.qm, "0000:06:00", 100)] == [(106, True)]
