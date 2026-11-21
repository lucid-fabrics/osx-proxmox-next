"""The graphics card flow without a screen: previews, fingerprints and the approval checks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from gpu_fakes import add_card, write
from osx_proxmox_next import gpu, host_setup, igpu_attach, intel_host
from osx_proxmox_next.gpu_flow import (
    Choice, Preview, approval_record, attach_differs, build_before, build_preview, card_facts, card_verdict, changes_text,
    need_rows, needs_typed, part_steps, plan_fingerprint, plan_for_approval, plan_lines, preview_vm_plan,
    replan_attach, same_plan, short_card_name, stop_mac, vm_fingerprint)
from osx_proxmox_next.gpu_setup import GpuCard
from test_gpu_setup import SEQUOIA, Env, ready

HOST_DRIVER = {0x1002: "amdgpu", 0x10DE: "nvidia"}


def env_with(tmp_path, vendor=0x1002, device=0x73FF, audio=0xAB28, bdf="0000:06:00", driver=None, **kw):
    env = Env(tmp_path, **kw)
    add_card(env.root, bdf=bdf, vendor=vendor, device=device, audio=audio, driver=driver or HOST_DRIVER[vendor])
    return env


def card_of(s, bdf="06:00") -> GpuCard:
    return next(c for c in s.scan() if bdf in c.bdf)


def rebind(env):
    for dev in (env.root / "sys/bus/pci/devices").iterdir():
        link = dev / "driver"
        if link.is_symlink():
            link.unlink()
            target = env.root / "sys/bus/pci/drivers/vfio-pci"
            target.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)


# ── helpers ──────────────────────────────────────────────────────────

def test_short_card_names_keep_the_marketing_name():
    assert short_card_name("Advanced Micro Devices, Inc. [AMD/ATI] Navi 23 [Radeon RX 6600]") == "Radeon RX 6600"
    assert short_card_name("NVIDIA Corporation GA106 [GeForce RTX 3060]") == "GeForce RTX 3060"
    assert short_card_name("Plain card") == "Plain card"
    assert short_card_name("Vendor [AMD/ATI]") == "Vendor [AMD/ATI]"


def test_fingerprints_ignore_the_stop_step_but_not_the_settings():
    steps = [SimpleNamespace(title="Stop the VM first", argv=["qm", "stop", "1"]),
             SimpleNamespace(title="Attach GPU", argv=["qm", "set", "1", "--hostpci0", "x"])]
    base = vm_fingerprint("0000:06:00", 100, "gpu", steps)
    assert base == vm_fingerprint("0000:06:00", 100, "gpu", steps[1:])
    assert base != vm_fingerprint("0000:06:00", 100, "console", steps)
    assert base != vm_fingerprint("0000:06:00", 101, "gpu", steps)
    plan = {"changes": [{"path": "/etc/a", "new": "x"}], "commands": [{"argv": ["update-initramfs"]}]}
    assert plan_fingerprint(plan) == plan_fingerprint({**plan}) != plan_fingerprint({**plan, "commands": []})


def test_plan_lines_use_todays_prefixes_and_careful_for_the_typed_override():
    plan = host_setup.new_plan()
    plan["refusals"] = ["This card is the host's only display adapter", "Some other stop"]
    plan["hard_warnings"] = ["h"]
    plan["warnings"], plan["notes"], plan["manual"], plan["skipped"] = ["w"], ["n"], ["m"], ["s"]
    assert plan_lines(plan) == ["Careful: This card is the host's only display adapter", "Refused: Some other stop",
                                "HARD WARNING: h", "Warning: w", "Note: n", "Manual step: m", "OK: s"]
    assert needs_typed(plan)
    prev = Preview(plan=plan)
    assert prev.refusals == ["Some other stop"] and prev.bios_failing == []


def test_the_verdict_under_the_card_is_one_plain_sentence_per_kind():
    native = GpuCard("0000:06:00", "amd", 0x73FF, "Radeon", "native", "Works natively on macOS 12 to 26", "d")
    nullmoth = GpuCard("0000:01:00", "nvidia", 0x2504, "GeForce", "nullmoth", "Works with macOS 15.5 to 26", "d")
    intel = GpuCard("0000:00:02", "intel", 0x46A6, "Iris", "intel", "Works with macOS Ventura to Sequoia", "d")
    assert card_verdict(native) == "Works in macOS without extra drivers"
    assert card_verdict(nullmoth) == "Works with the NullMoth driver (beta)"
    assert card_verdict(intel) == "Works with macOS Ventura to Sequoia"


def test_the_card_facts_head_names_the_card_its_address_and_the_host():
    text = card_facts(GpuCard("0000:06:00", "amd", 0x73FF, "Radeon RX 6600", "native", "Works natively", "Navi 23."),
                      ["CPU: INTEL", "IOMMU: on"])
    assert text == ("Card: Radeon RX 6600\nAddress: 0000:06:00\nWorks natively\nNavi 23.\n\n"
                    "This host: CPU: INTEL   IOMMU: on\n")


# ── B1: a card on its host driver is work to do, not a refusal ───────

@pytest.mark.parametrize("vendor,device,audio,bdf", [(0x1002, 0x73FF, 0xAB28, "0000:06:00"),
                                                     (0x10DE, 0x2504, 0x228E, "0000:01:00")])
def test_a_card_on_its_host_driver_previews_without_a_refusal(tmp_path, vendor, device, audio, bdf):
    env = env_with(tmp_path, vendor, device, audio, bdf)
    s = env.session()
    card = card_of(s, bdf[5:])
    prev = build_preview(s, Choice(card, 100, "gpu" if vendor == 0x1002 else "console"))
    assert prev.refusals == [] and prev.error == ""
    assert len(prev.host_pending) == 1 and gpu.BINDING_ISSUE in prev.host_pending[0]
    assert prev.vm_plan.ok and prev.vm_plan.steps                       # the steps are shown, so they can be approved
    assert ("●", "Host setup", "3 files change") in need_rows(prev)
    raw = s.preview_vm(card, 100, prev.display, True)
    assert raw.issues and gpu.is_host_readiness_issue(raw.issues[0])    # without the assumption it would refuse


@pytest.mark.parametrize("vendor,device,audio,bdf", [(0x1002, 0x73FF, 0xAB28, "0000:06:00"),
                                                     (0x10DE, 0x2504, 0x228E, "0000:01:00")])
def test_the_plan_approved_before_the_setup_equals_the_plan_after_the_restart(tmp_path, vendor, device, audio, bdf):
    env = env_with(tmp_path, vendor, device, audio, bdf)
    s = env.session()
    display = "gpu" if vendor == 0x1002 else "console"
    before = build_preview(s, Choice(card_of(s, bdf[5:]), 100, display))
    s.choose(bdf[5:], 100, display, ack=vendor == 0x10DE)
    ready(env)
    rebind(env)                                                       # the host setup ran and the host restarted
    after = env.session()
    after.state = s.state
    plan = after.vm_plan(allow_stop=True)
    assert plan.ok
    assert vm_fingerprint(bdf, 100, display, plan.steps) == vm_fingerprint(bdf, 100, display, before.vm_plan.steps)
    assert approval_record(before, typed=False, release="")["vm_fp"] == vm_fingerprint(bdf, 100, display, plan.steps)
    assert not attach_differs(approval_record(before, typed=False, release=""), after.state, plan)


def test_a_vm_side_problem_is_still_a_refusal_on_a_card_on_its_host_driver(tmp_path):
    env = env_with(tmp_path, vms={100: ("stopped", "name: plain\n")})
    s = env.session()
    prev = build_preview(s, Choice(card_of(s), 100, "gpu"))
    assert prev.refusals and "not a macOS VM made by this tool" in prev.refusals[0]
    assert prev.host_pending and prev.vm_plan.steps == []


def test_a_host_ready_card_has_nothing_pending(tmp_path):
    env = env_with(tmp_path, driver="vfio-pci")
    s = env.session()
    plan, pending = preview_vm_plan(s, card_of(s), 100, "gpu")
    assert pending == [] and plan.ok


def test_the_assumption_skips_only_the_host_readiness_checks(tmp_path):
    env = env_with(tmp_path, bdf="0000:06:00")
    root = env.root / "sys"
    plain = gpu.check_gpu("06:00", 0x1002, lambda _d: True, "x", root)
    assert any(gpu.BINDING_ISSUE in i for i in plain.issues) and plain.hints
    with gpu.assuming_host_ready():
        assert gpu.host_ready_assumed()
        assumed = gpu.check_gpu("06:00", 0x1002, lambda _d: True, "x", root)
        missing = gpu.check_gpu("07:00", 0x1002, lambda _d: True, "x", root)
    assert assumed.issues == [] and assumed.hints == []
    assert not gpu.host_ready_assumed()
    assert missing.issues == ["No PCI device at 0000:07:00.0 on this host."]   # a real problem is never hidden


def test_a_card_without_an_iommu_group_is_not_a_refusal_before_the_host_setup(tmp_path):
    env = env_with(tmp_path)
    for dev in (env.root / "sys/bus/pci/devices").iterdir():
        (dev / "iommu_group").unlink()
    root = env.root / "sys"
    assert any(gpu.NO_IOMMU_ISSUE in i for i in gpu.check_gpu("06:00", 0x1002, lambda _d: True, "x", root).issues)
    with gpu.assuming_host_ready():
        assert gpu.check_gpu("06:00", 0x1002, lambda _d: True, "x", root).issues == []


def test_the_intel_igpu_findings_the_host_setup_fixes_are_left_out_under_the_assumption(monkeypatch):
    engine = {"ok": False, "device": 0x46A6, "info": None, "layout": {"ok": True}, "driver": "i915",
              "issues": ["The host still uses the iGPU (driver: i915). Run gpu-setup.",
                         "IOMMU is not active for the iGPU (no iommu_group). Enable VT-d in the BIOS.",
                         "Could not read the iGPU memory registers with setpci."],
              "warnings": ["w"], "hints": []}
    monkeypatch.setattr(intel_host.igpu_engine, "check", lambda *a, **k: engine)
    assert len(intel_host.check_intel_gpu().issues) == 3
    with gpu.assuming_host_ready():
        assert intel_host.check_intel_gpu().issues == ["Could not read the iGPU memory registers with setpci."]


# ── the approval ─────────────────────────────────────────────────────

def test_the_before_dialog_has_the_warnings_the_notes_and_the_notice_in_todays_words(tmp_path):
    env = env_with(tmp_path, vendor=0x10DE, device=0x2504, audio=0x228E, bdf="0000:01:00")
    s = env.session()
    prev = build_preview(s, Choice(card_of(s, "01:00"), 100, "console"))
    before = build_before(prev)
    assert before.nvidia and not before.empty and before.notice_title == "NVIDIA GPU support (Beta)"
    assert "Warning: The guest must run macOS 15.5 or newer. Older Sequoia builds are not supported." in before.warnings
    assert before.notice_lead.startswith("NVIDIA: the Proxmox console goes black")
    assert not before.phrase
    assert build_before(build_preview(s, Choice(card_of(s, "01:00"), None, "console"))).nvidia   # not tied to a Mac


def test_a_clean_host_has_nothing_to_confirm(tmp_path):
    env = env_with(tmp_path)
    s = env.session()
    assert build_before(build_preview(s, Choice(card_of(s), 100, "gpu"))).empty


def test_the_only_display_adapter_wants_the_typed_phrase_and_the_phrase_lifts_the_stop(tmp_path):
    env = env_with(tmp_path)
    write(env.root, "/sys/bus/pci/devices/0000:06:00.0/boot_vga", "1\n")
    s = env.session()
    prev = build_preview(s, Choice(card_of(s), 100, "gpu"))
    assert prev.typed_needed and any(line.startswith("Careful: ") for line in build_before(prev).warnings)
    typed = build_preview(s, Choice(card_of(s), 100, "gpu"), typed=True)
    assert typed.plan["refusals"] == [] and prev.plan["refusals"]       # the phrase lifts the stop
    assert approval_record(typed, typed=True, release="")["allow_no_console"] is True
    assert approval_record(prev, typed=False, release="")["allow_no_console"] is False


def test_a_plan_that_differs_from_the_approval_is_made_again_without_the_old_phrase(tmp_path):
    """S6: "I understand" typed for plan A is not part of plan B."""
    env = env_with(tmp_path)
    write(env.root, "/sys/bus/pci/devices/0000:06:00.0/boot_vga", "1\n")
    s = env.session()
    s.choose("06:00", 100, "gpu")
    old = {"host_fp": "from another plan", "allow_no_console": True, "typed": True}
    plan, same = plan_for_approval(s, old)
    assert not same and any("only display adapter" in r for r in plan["refusals"])
    typed_plan, _ = plan_for_approval(s, old, typed_now=True)
    assert typed_plan["refusals"] == []
    matching = {"host_fp": plan_fingerprint(typed_plan), "allow_no_console": True, "typed": True}
    kept, same = plan_for_approval(s, matching)
    assert same and kept["refusals"] == []


def test_the_attach_check_compares_the_fingerprint_and_a_missing_one_always_asks(tmp_path):
    env = env_with(tmp_path, driver="vfio-pci")
    s = env.session()
    prev = build_preview(s, Choice(card_of(s), 100, "gpu"))
    s.choose("06:00", 100, "gpu")
    plan = s.vm_plan(allow_stop=True)
    record = approval_record(prev, typed=False, release="")
    assert not attach_differs(record, s.state, plan)
    assert attach_differs({**record, "vm_fp": "*"}, s.state, plan)       # no wildcard
    assert attach_differs(None, s.state, plan) and attach_differs({}, s.state, plan)


def test_the_attach_is_replanned_against_what_was_shown(tmp_path):
    env = env_with(tmp_path, driver="vfio-pci", vms={100: ("stopped", SEQUOIA)})
    s = env.session()
    s.choose("06:00", 100, "gpu")
    plan = s.vm_plan(allow_stop=True)
    shown = vm_fingerprint("0000:06:00", 100, "gpu", plan.steps)
    assert replan_attach(s, shown, False)[1] is True
    env.qm.vms[100] = ("stopped", SEQUOIA.replace("-cpu host", "-cpu host -device edited"))
    again, same = replan_attach(s, shown, False)
    assert same is False and again.ok
    env.qm.vms[100] = ("stopped", "name: plain\n")
    broken, same = replan_attach(s, shown, False)
    assert same is False and not broken.ok


def test_stopping_a_mac_waits_until_it_is_down(tmp_path):
    env = env_with(tmp_path, driver="vfio-pci", vms={100: ("running", SEQUOIA)})
    s = env.session()
    polls = []
    original = env.qm.run

    def run(argv):
        if argv[:2] == ["qm", "status"]:
            polls.append(1)
            if len(polls) >= 3:
                env.qm.vms[100] = ("stopped", SEQUOIA)
        return original(argv)

    env.qm.run = run
    stop_mac(s, 100, sleep=lambda _s: None)
    assert ["qm", "stop", "100"] in env.qm.calls and len(polls) >= 3


def test_stopping_a_mac_that_will_not_stop_says_so(tmp_path):
    env = env_with(tmp_path, driver="vfio-pci", vms={100: ("running", SEQUOIA)})
    with pytest.raises(RuntimeError, match="still running after 0 seconds"):
        stop_mac(env.session(), 100, wait=0, sleep=lambda _s: None)
    failing = env_with(tmp_path / "b", driver="vfio-pci", vms={100: ("running", SEQUOIA)}).session()
    failing.stop_vm = lambda vmid: (False, "no")
    with pytest.raises(RuntimeError, match="Could not stop VM 100: no"):
        stop_mac(failing, 100)


def test_the_panel_rows_and_your_part_follow_the_preview(tmp_path):
    env = env_with(tmp_path, driver="vfio-pci")
    ready(env)
    s = env.session()
    prev = build_preview(s, Choice(card_of(s), None, "gpu"))
    rows = need_rows(prev)
    assert rows[0] == ("✓", "BIOS settings", "already right") and all(r[1] != "Attach to the Mac" for r in rows)
    assert part_steps(prev)[0] == ("Press enter", "")
    broken = Preview(plan={"refusals": [], "changes": [], "reboot_required": True, "optional": False, "commands": [],
                           "manual": []}, items=[{"ok": False}], vmid=100)
    assert part_steps(broken) == [("Change the BIOS settings", "we show exactly how"), ("Press enter", ""),
                                  ("Restart when asked", "")]
    assert ("●", "BIOS settings", "1 to change") in need_rows(broken)


def test_the_changes_text_has_the_head_the_files_and_the_vm_steps(tmp_path):
    env = env_with(tmp_path)
    s = env.session()
    prev = build_preview(s, Choice(card_of(s), 100, "gpu"))
    text = changes_text(prev.plan, prev.vm_plan, 100, "HEAD\n")
    assert text.startswith("HEAD\n") and "Host files and commands (each file is backed up first, in " in text
    assert "VM 100 settings (a config snapshot is saved first):" in text and "01. " in text
    assert "No Mac is chosen yet" in changes_text(prev.plan, None, None)
    assert "nothing to change on the host" in changes_text(host_setup.new_plan(), None, 100)


def test_the_before_dialog_lists_the_not_on_ssh_warning_of_the_plan_that_will_run(tmp_path, monkeypatch):
    """SF1: the card is the host's only display and the user is not on SSH: the warning is shown before the phrase."""
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    env = env_with(tmp_path)
    write(env.root, "/sys/bus/pci/devices/0000:06:00.0/boot_vga", "1\n")
    s = env.session()
    prev = build_preview(s, Choice(card_of(s), 100, "gpu"))
    ssh = "Warning: You are not on an SSH session. Make sure you can log in over the network before you reboot."
    assert ssh in build_before(prev).warnings
    assert ssh in plan_lines(build_preview(s, Choice(card_of(s), 100, "gpu"), typed=True).plan)
    assert plan_fingerprint(prev.plan) == plan_fingerprint(build_preview(s, Choice(card_of(s), 100, "gpu"), typed=True).plan)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 5000 10.0.0.1 22")   # on SSH there is nothing extra to say
    assert ssh not in build_before(build_preview(s, Choice(card_of(s), 100, "gpu"))).warnings


def test_same_plan_tells_a_changed_plan_from_an_unchanged_one(tmp_path):
    env = env_with(tmp_path)
    s = env.session()
    card = card_of(s)
    assert same_plan(build_preview(s, Choice(card, 100, "gpu")), build_preview(s, Choice(card, 100, "gpu")))
    assert not same_plan(build_preview(s, Choice(card, 100, "gpu")), build_preview(s, Choice(card, 100, "console")))
    shown = build_preview(s, Choice(card, 100, "gpu"))
    env.qm.vms[100] = ("stopped", SEQUOIA.replace("args: ", "args: -smbios type=1 "))   # the Mac was edited
    assert not same_plan(shown, build_preview(s, Choice(card, 100, "gpu")))


def test_the_igpu_attach_fingerprint_survives_the_zips_landing_in_the_cache(tmp_path):
    """N4: the zip hash is in the attach script only once the file is there. It must not make the attach differ."""
    wex, amfi = tmp_path / "wex.zip", tmp_path / "amfi.zip"
    before = [igpu_attach.oc_step("100", "attach", "Patch OpenCore", wex, amfi)]
    wex.write_bytes(b"wex"), amfi.write_bytes(b"amfi")
    after = [igpu_attach.oc_step("100", "attach", "Patch OpenCore", wex, amfi)]
    assert before[0].argv != after[0].argv                                  # the raw scripts do differ
    assert vm_fingerprint("0000:00:02", 100, "console", before) == vm_fingerprint("0000:00:02", 100, "console", after)
    other = [igpu_attach.oc_step("100", "restore", "Patch OpenCore", wex, amfi)]
    assert vm_fingerprint("0000:00:02", 100, "console", other) != vm_fingerprint("0000:00:02", 100, "console", after)
