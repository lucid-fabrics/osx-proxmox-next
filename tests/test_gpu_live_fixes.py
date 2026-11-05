"""Regressions from the live hardware run: stopped holders, one undo for every entry point, console copy.

Live scenario: card 0000:06:00 (RTX 3060, boot_vga=1, bound to vfio-pci, efifb off) is listed by the
stopped VM 120 while VM 990 gets NullMoth. Hand-written (no generated scaffold)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from gpu_fakes import add_card, write
from nullmoth_fakes import MAC_CFG
from osx_proxmox_next import cli, gpu, gpu_attach, gpu_setup, gpu_setup_cli, nullmoth
from test_gpu_parity import BASH, MAC, NV_STUBS, bash, block_file, nvidia_env, vm  # noqa: F401
from test_gpu_setup import SEQUOIA, Env
from test_gpu_setup import _no_real_vm_listing  # noqa: F401

HOLDER = "hostpci0: 0000:06:00,pcie=0\n"


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    gpu_setup_cli.add_parser(parser.add_subparsers(dest="cmd"))
    return parser.parse_args(["gpu-setup", *argv])


def run(env: Env, *argv: str, interactive: bool = False, inputs=None, session=None):
    out: list[str] = []
    queue = list(inputs or [])
    code = gpu_setup_cli.run(parse(*argv), session or env.session(), lambda p: (out.append(p), queue.pop(0))[1],
                             out.append, interactive=interactive)
    return code, "\n".join(out)


def nvidia_env_host(tmp_path, holder_status="stopped", boot_vga=True, driver="vfio-pci"):
    env = Env(tmp_path, cpu="intel", vms={100: ("stopped", SEQUOIA), 120: (holder_status, HOLDER)})
    add_card(env.root, bdf="0000:06:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3, boot_vga=boot_vga,
             driver=driver)
    return env


# ── 1. a stopped VM owning the card is a warning, a running one blocks ──

def test_stopped_holder_is_a_note_and_the_card_stays_selectable(tmp_path):
    card = nvidia_env_host(tmp_path).session().scan()[0]
    assert card.selectable and card.users == [(120, False)]
    text = gpu_setup.card_text(card)
    assert "Note: Also assigned to VM 120 (stopped)." in text
    assert "Can't use" not in text and text.startswith(">>")


def test_mapping_holder_follows_the_same_rule(tmp_path):
    env = Env(tmp_path, cpu="intel", vms={100: ("stopped", SEQUOIA), 120: ("stopped", "hostpci0: mapping=gpu-a,pcie=1\n")})
    add_card(env.root, bdf="0000:06:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    write(env.root, "/etc/pve/mapping/pci.cfg", "gpu-a\n\tmap id=10de:2504,node=pve,path=0000:06:00.0\n")
    assert env.session().scan()[0].stopped_users == [120]
    env.qm.vms[120] = ("running", "hostpci0: mapping=gpu-a,pcie=1\n")
    card = env.session().scan()[0]
    assert not card.selectable and card.why_not == "In use by VM 120 (running)"


def test_choose_needs_the_explicit_confirm_for_a_stopped_holder(tmp_path):
    s = nvidia_env_host(tmp_path).session()
    issues = s.choose("06:00", 100, "console", ack=True)
    assert issues and "Also assigned to VM 120 (stopped)" in issues[0] and "Confirm" in issues[0]
    assert s.state["gpu"] is None
    assert s.choose("06:00", 100, "console", ack=True, share_ok=True) == []


def test_walkthrough_with_a_stopped_holder_asks_and_never_says_nothing_can_run(tmp_path):
    env = nvidia_env_host(tmp_path)
    code, out = run(env, "--gpu", "0000:06:00", "--vm", "100", "--accept-nullmoth-risk")
    assert code == gpu_setup_cli.EXIT_REFUSED and "Nothing here can run macOS graphics" not in out
    assert "WARN: Also assigned to VM 120 (stopped)" in out and "Nothing was changed" in out
    session = env.session()
    code, out = run(env, "--gpu", "0000:06:00", "--vm", "100", "--accept-nullmoth-risk", "--yes", session=session)
    assert "Nothing here can run macOS graphics" not in out and "REFUSED" not in out.split("WARN")[0]
    assert session.state["gpu"]["bdf"] == "0000:06:00" and session.state["vm"] == 100


def test_running_holder_blocks_with_an_honest_message(tmp_path):
    env = nvidia_env_host(tmp_path, holder_status="running")
    code, out = run(env, "--gpu", "0000:06:00", "--vm", "100", "--accept-nullmoth-risk", "--yes")
    assert code == gpu_setup_cli.EXIT_REFUSED and "Can't use: In use by VM 120 (running)" in out
    assert "Nothing here can run macOS graphics" not in out and "No supported card can be used right now" in out


def test_nothing_here_message_only_when_no_card_is_supported(tmp_path):
    env = Env(tmp_path, cpu="intel")
    add_card(env.root, device=0x73DF)  # Navi 22: no Apple driver
    code, out = run(env, "--yes")
    assert code == 2 and "Nothing here can run macOS graphics" in out


def test_attach_commands_use_the_same_rule_and_wording(tmp_path):
    msg = gpu.shared_note(120)
    assert msg == ("Also assigned to VM 120 (stopped). Only one of them can run at a time; "
                   "Proxmox refuses to start the second.")
    assert gpu.running_user_issue(120) == "The card is in use by running VM 120."


# ── 2. one record, one engine: gpu-setup --undo undoes an nvidia-attach ──

@pytest.fixture
def attached(tmp_path, monkeypatch):
    """VM 100 as nvidia-attach leaves it: the record on disk, hostpci0 and the MMIO window set, no walkthrough state."""
    env = Env(tmp_path, cpu="intel", vms={100: ("stopped", SEQUOIA)})
    add_card(env.root)
    rec = gpu.new_record(100, "0000:01:00", "nvidia", gpu.parse_config(SEQUOIA), gpu.NVIDIA_PROFILE,
                         "0000:01:00,pcie=0", oc="nullmoth")
    gpu.STATE_DIR.mkdir(parents=True, exist_ok=True)
    gpu.record_path(100).write_text(json.dumps(gpu.asdict(rec)))
    env.qm.vms[100] = ("stopped", SEQUOIA.rstrip("\n") + f" {gpu.MMIO_ARG}\nhostpci0: 0000:01:00,pcie=0\n")
    applied = []

    def fake_apply(steps, execute=False, adapter=None, on_step=None):
        from osx_proxmox_next.executor import ApplyResult, StepResult
        applied.append([s.title for s in steps])
        return ApplyResult(True, [StepResult("s", "c", True, 0, "")], Path("/tmp/log"))

    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan", fake_apply)
    return env, applied


def test_gpu_setup_undo_reverts_an_nvidia_attach(attached):
    env, applied = attached
    s = env.session()
    assert s.state["vm_records"] == []  # the walkthrough never heard of this attach
    code, out = run(env, "--undo", session=s)
    assert code == 0 and "VM 100: GPU settings restored." in out and "Nothing to undo" not in out
    assert applied and any("Restore the previous OpenCore config" in t for t in applied[0])


def test_the_same_plan_comes_from_nvidia_detach_and_undo(attached):
    env, _applied = attached
    plan = gpu_attach.prepare_detach(100, adapter=env.qm)
    assert plan.ok and [s.title for s in plan.steps] == [s.title for s in nullmoth.prepare_detach(100, adapter=env.qm).steps]
    assert gpu.record_vmids() == [100]


def test_nvidia_detach_removes_the_vm_from_the_walkthrough_list(attached, monkeypatch):
    env, _applied = attached
    s = env.session()
    s.state["vm_records"] = [100]
    s.save()
    monkeypatch.setattr(cli, "_run_plan", lambda plan, label: 0)
    monkeypatch.setattr(gpu_attach, "prepare_detach", lambda vmid: gpu_attach.AttachPlan(vmid))
    monkeypatch.setattr(gpu_setup, "Session", lambda: env.session())
    assert cli._run_nvidia_detach(argparse.Namespace(vmid=100, dry_run=False)) == 0
    assert env.session().state["vm_records"] == []


def test_undo_with_nothing_recorded_does_not_claim_success(tmp_path):
    env = Env(tmp_path, cpu="intel", vms={100: ("stopped", SEQUOIA)})
    add_card(env.root)
    code, out = run(env, "--undo")
    assert code == 0 and "Nothing to undo: no GPU setup record found" in out
    assert "Everything this setup changed is back" not in out and "nvidia-detach" not in out


def test_undo_with_nothing_recorded_points_at_nvidia_detach_when_markers_exist(tmp_path):
    marked = f"name: x\nargs: -cpu host {gpu.MMIO_ARG}\nhostpci0: 0000:01:00,pcie=0\n"
    env = Env(tmp_path, cpu="intel", vms={100: ("stopped", SEQUOIA), 120: ("stopped", marked)})
    add_card(env.root)
    code, out = run(env, "--undo")
    assert code == 0 and "VM 120 has NullMoth settings" in out and "nvidia-detach 120" in out
    assert "VM 100 has NullMoth" not in out and "Everything this setup changed is back" not in out


# ── 3. console copy ──

def test_every_nvidia_text_says_the_console_goes_black_and_asks_for_remote_access():
    first = nullmoth.NEXT_STEPS[0]
    assert "console goes black" in first and "Once the NullMoth driver loads" in first
    assert "stays on the emulated display" not in "\n".join(nullmoth.NEXT_STEPS)
    prep = "\n".join(nullmoth.GUEST_PREP)
    assert "Remote Login" in prep and "Screen Sharing" in prep and "System Settings > General > Sharing" in prep
    assert "open vnc://<ip>" in prep and "launchctl load -w /System/Library/LaunchDaemons/ssh.plist" in prep
    assert "4 or more lines" in nullmoth.NEXT_STEPS[3] and "grep -i nullmoth" in nullmoth.NEXT_STEPS[3]


def test_first_boot_text_and_the_vm_step_checklist(tmp_path):
    env = nvidia_env_host(tmp_path, holder_status="stopped")
    s = env.session()
    assert s.choose("06:00", 100, "console", ack=True, share_ok=True) == []
    assert "stays on the Proxmox console" not in s.first_boot_text() and "goes black" in s.first_boot_text()


def test_vm_step_asks_the_guest_checklist_before_attaching(tmp_path, monkeypatch):
    env = nvidia_env_host(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "console", ack=True, share_ok=True)
    s.goto(7)
    applied = []
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan", lambda *a, **k: applied.append(1))
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)
    monkeypatch.setattr("osx_proxmox_next.oc_builder.ensure_amfipass", lambda d: None)
    monkeypatch.setattr(gpu_setup.Session, "vm_plan", lambda self, allow_stop=False: nullmoth.AttachPlan(100))
    out: list[str] = []
    ns = parse("--vm", "100")
    code = gpu_setup_cli._step_vm(s, gpu_setup_cli._Io(lambda p: "", out.append, False), ns)
    text = "\n".join(out)
    assert "Before you attach (required)" in text and "Remote Login and Screen Sharing" in text
    assert code == gpu_setup_cli.EXIT_REFUSED and applied == [] and "Nothing was changed" in text


# ── 4. host console label ──

def test_boot_vga_on_vfio_without_a_framebuffer_is_not_labelled_host_console(tmp_path):
    env = nvidia_env_host(tmp_path, holder_status="stopped")
    card = env.session().scan()[0]
    assert card.boot_vga and not card.host_console
    text = gpu_setup.card_text(card)
    assert "host console" not in text and "The firmware shows its boot screen on this card." in text


def test_boot_vga_with_a_host_driver_or_framebuffer_is_the_host_console(tmp_path):
    env = nvidia_env_host(tmp_path / "a", driver="nouveau")
    card = env.session().scan()[0]
    assert card.host_console and "host console" in gpu_setup.card_text(card)
    assert "The firmware shows" not in gpu_setup.card_text(card)
    env = nvidia_env_host(tmp_path / "b")
    write(env.root, "/proc/iomem", "  100000000-1002fffff : efifb\n")  # inside BAR0 of the fake card
    card = env.session().scan()[0]
    assert card.host_console and "host console" in gpu_setup.card_text(card)


# ── bash twins ──

def bash_env(tmp_path, vm_env, holder_status="stopped", boot_vga=True, driver="vfio-pci"):
    env = Env(tmp_path / "py", cpu="intel")
    add_card(env.root, bdf="0000:06:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3, boot_vga=boot_vga,
             driver=driver)
    return env.root, {**vm_env, "GPU_ROOT": str(env.root), "GPU_SYSFS": str(env.root / "sys"),
                      "GPU_CMDLINE": str(env.root / "proc/cmdline"), "GPU_SNAPSHOT_BASE": str(tmp_path / "snap")}


def test_bash_stopped_holder_is_a_note_and_running_blocks(bash, vm, tmp_path):
    vm(120, "stopped", HOLDER)
    root, env = bash_env(tmp_path, vm.env)
    snippet = 'gpu_scan; for l in "${GPU_CARDS[@]}"; do gpu_card_selectable "$l" && echo SELECTABLE; gpu_card_text "$l"; done'
    out = bash(snippet, env=env).stdout
    assert "SELECTABLE" in out and "Also assigned to VM 120 (stopped)" in out and "Can't use" not in out
    assert "host console" not in out and "The firmware shows its boot screen on this card." in out
    vm(120, "running", HOLDER)
    out = bash(snippet, env=env).stdout
    assert "SELECTABLE" not in out and "Can't use: In use by VM 120 (running)" in out
    res = bash("gpu_setup --yes --gpu 06:00 --vm 100", env=env, expect=2)
    assert "No supported card can be used right now" in res.stdout and "Nothing here can run" not in res.stdout


def test_bash_stopped_holder_needs_the_confirm_and_never_hard_refuses(bash, vm, tmp_path):
    vm(120, "stopped", HOLDER)
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    root, env = bash_env(tmp_path, vm.env)
    res = bash("gpu_setup --gpu 06:00 --vm 100 --accept-nullmoth-risk", env=env, expect=2)  # no tty, no --yes
    assert "WARN: Also assigned to VM 120 (stopped)" in res.stdout and "Nothing here can run" not in res.stdout
    assert "Nothing was changed" in res.stdout
    res = bash("gpu_setup --yes --gpu 06:00 --vm 100 --accept-nullmoth-risk", env=env, expect=None)
    assert "Nothing here can run" not in res.stdout and "Also assigned to VM 120 (stopped)" in res.stdout


def test_bash_host_console_follows_the_python_rule(bash, vm, tmp_path):
    vm(120, "stopped", "")
    root, env = bash_env(tmp_path, vm.env, driver="nouveau")
    out = bash('gpu_scan; gpu_card_text "${GPU_CARDS[0]}"', env=env).stdout
    assert "host console" in out and "The firmware shows" not in out
    root, env = bash_env(tmp_path / "x", vm.env)
    write(root, "/proc/iomem", "  100000000-1002fffff : efifb\n")
    assert "host console" in bash('gpu_scan; gpu_card_text "${GPU_CARDS[0]}"', env=env).stdout


def test_bash_nvidia_attach_prints_the_checklist_and_the_shared_wording(bash, vm, tmp_path):
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    vm(101, "stopped", "hostpci0: 0000:01:00,pcie=0\n")
    res = bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk --yes", env=nvidia_env(tmp_path, vm.env))
    assert "Before you attach (required)" in res.stdout and "Remote Login and Screen Sharing" in res.stdout
    assert gpu.shared_note(101) in res.stdout
    assert "the Proxmox console goes black" in res.stdout and "4 or more lines" in res.stdout


def test_bash_gpu_setup_undo_reverts_an_nvidia_attach_exactly(bash, vm, tmp_path):
    original = MAC.replace("sequoia", "tahoe")
    vm(100, "stopped", original)
    env = nvidia_env(tmp_path, vm.env)
    bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk --yes", env=env)
    vm(100, "stopped", original.replace("-cpu host", f"-cpu host {gpu.MMIO_ARG}") + "hostpci0: 0000:01:00,pcie=0\n")
    (Path(vm.env["QM_DIR"]) / "calls.log").unlink()
    env = {**env, "GPU_STATE_DIR": str(tmp_path / "state")}
    res = bash(NV_STUBS + "gpu_setup --undo", env=env)
    assert "VM 100: GPU settings restored" in res.stdout and "NMOC 100 restore" in res.stdout
    assert vm.calls() == ["qm set 100 --delete hostpci0", 'qm set 100 --args -device isa-applesmc,osk="x" -cpu host']
    assert not (tmp_path / "state/gpu-attach-100.json").exists()


def test_bash_undo_with_nothing_recorded(bash, vm, tmp_path):
    vm(100, "stopped", MAC)
    marked = MAC.replace("sequoia", "tahoe").replace("-cpu host", f"-cpu host {gpu.MMIO_ARG}") + "hostpci0: 0000:01:00,pcie=0\n"
    vm(120, "stopped", marked)
    res = bash("gpu_setup --undo", env={**vm.env, "GPU_STATE_DIR": str(tmp_path / "state")})
    assert "Nothing to undo: no GPU setup record found" in res.stdout
    assert "Everything this setup changed is back" not in res.stdout
    assert "VM 120 has NullMoth settings" in res.stdout and "nvidia-detach 120" in res.stdout
    assert "VM 100 has NullMoth" not in res.stdout


def test_bash_nvidia_detach_drops_the_vm_from_the_walkthrough_list(bash, vm, tmp_path):
    original = MAC.replace("sequoia", "tahoe")
    vm(100, "stopped", original)
    env = nvidia_env(tmp_path, vm.env)
    state_dir = Path(env["GPU_ROOT"]) / "var/lib/osx-proxmox-next"
    env["GPU_STATE_DIR"] = str(state_dir)
    bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk --yes", env=env)
    bash("gpu_state_save '{\"vm_records\": [100]}'", env=env)
    vm(100, "stopped", original.replace("-cpu host", f"-cpu host {gpu.MMIO_ARG}") + "hostpci0: 0000:01:00,pcie=0\n")
    bash(NV_STUBS + "nvidia_detach 100", env=env)
    state = json.loads((state_dir / "gpu-setup.json").read_text())
    assert state["vm_records"] == []


def test_docs_and_readme_copy():
    root = Path(__file__).resolve().parent.parent
    for rel in ("docs/docs/guides/gpu-passthrough.md", "docs/docs/guides/nvidia-nullmoth.md"):
        doc = (root / rel).read_text()
        assert "console goes black" in doc and "Remote Login" in doc and "Screen Sharing" in doc, rel
        assert "stays on the emulated display" not in doc
    assert "4 or more lines" in (root / "docs/docs/guides/nvidia-nullmoth.md").read_text()
    assert "console goes black" in (root / "README.md").read_text()


# ── undo skips a record that belongs to another VM, confirms follow the selection ──

SKIPPED = ("Skipped the record for VM 100: the VM changed since the card was attached, so nothing was "
           "reverted. Check its hostpci0, args and vga by hand.")


def test_undo_skips_the_record_of_a_recreated_vm_and_keeps_it_as_stale(attached):
    env, applied = attached
    env.qm.vms[100] = ("stopped", env.qm.vms[100][1].replace("macos-sequoia", "another-vm"))
    code, out = run(env, "--undo")
    assert code == 0 and SKIPPED in out and "GPU settings restored" not in out and not applied
    assert gpu.record_vmids() == []
    assert len(list(gpu.STATE_DIR.glob("gpu-attach-100.json.stale-*"))) == 1
    code, out = run(env, "--undo")  # the second run is not stuck on the same record
    assert code == 0 and SKIPPED not in out


def test_undo_still_reverts_the_vm_the_record_was_written_for(attached):
    env, applied = attached
    code, out = run(env, "--undo")
    assert code == 0 and "GPU settings restored" in out and applied and "Skipped" not in out


def test_choose_refuses_a_holder_that_appeared_after_the_scan(tmp_path):
    env = nvidia_env_host(tmp_path)
    s = env.session()
    assert s.choose("0000:06:00", 100, "console", True, True, confirmed=[]) != []
    assert s.choose("0000:06:00", 100, "console", True, True, confirmed=[120]) == []


def test_cli_never_assumes_the_shared_card_warning_was_accepted(tmp_path):
    env = nvidia_env_host(tmp_path)
    code, out = run(env, "--vm", "100", "--gpu", "06:00", "--accept-nullmoth-risk")  # no --yes, not interactive
    assert code == gpu_setup_cli.EXIT_REFUSED and "Nothing was changed" in out


def test_tui_two_click_keys_follow_the_card_and_the_vm():
    from types import SimpleNamespace as NS
    from osx_proxmox_next._gpu_mixin import GpuSetupMixin as M
    a, b = NS(bdf="0000:06:00", stopped_users=[120]), NS(bdf="0000:07:00", stopped_users=[120])
    assert M._gp_share_key(a) != M._gp_share_key(b)
    assert M._gp_share_key(a) != M._gp_share_key(NS(bdf="0000:06:00", stopped_users=[120, 130]))
    s1 = NS(state={"vm": 100, "gpu": {"bdf": "0000:06:00"}})
    s2 = NS(state={"vm": 101, "gpu": {"bdf": "0000:06:00"}})
    assert M._gp_guest_key(s1) != M._gp_guest_key(s2)

    class Dummy(M):
        _gp_armed = None

        def _gp_result(self, *_a, **_k):
            pass

    d = Dummy()
    assert d._gp_confirm_click(M._gp_share_key(a), "x", "w") is False
    assert d._gp_confirm_click(M._gp_share_key(b), "x", "w") is False  # another card: armed again, not accepted
    assert d._gp_confirm_click(M._gp_share_key(b), "x", "w") is True


# ── standalone nvidia-attach asks the Screen Sharing question ──

def test_nvidia_attach_asks_the_guest_question_and_yes_answers_only_that(monkeypatch, tmp_path, capsys):
    from test_nullmoth import patch_cli
    applied = patch_cli(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.stdin", type("T", (), {"isatty": lambda self: False})())
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk"]) == 2
    assert "Nothing was changed" in capsys.readouterr().out and not applied
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--yes"]) == 2  # --yes never gives the risk ack
    assert not applied
    monkeypatch.setattr("sys.stdin", type("T", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk"]) == 0
    assert applied
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    applied.clear()
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk"]) == 2 and not applied


def test_bash_undo_skips_the_record_of_a_recreated_vm(bash, vm, tmp_path):
    from test_gpu_parity import put_record
    from test_gpu_hardening_bash import walkthrough_env
    root, state_dir, env = walkthrough_env(tmp_path, vm)
    state_dir.mkdir(parents=True, exist_ok=True)
    put_record(tmp_path, 100, "0000:06:00", "amd", None, None, "std", True, "")
    rec = json.loads((tmp_path / "state/gpu-attach-100.json").read_text())
    rec["fp"] = "an-older-vm"
    (state_dir / "gpu-attach-100.json").write_text(json.dumps(rec))
    vm(100, "stopped", MAC)
    res = bash("gpu_undo no", env=env)
    assert SKIPPED in res.stdout + res.stderr and vm.calls() == []
    assert not (state_dir / "gpu-attach-100.json").exists() and list(state_dir.glob("gpu-attach-100.json.stale-*"))


def test_bash_nvidia_attach_asks_the_guest_question(bash, vm, tmp_path):
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    env = nvidia_env(tmp_path, vm.env)
    out = bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk", env=env, expect=2)
    assert "Nothing was changed" in out.stdout + out.stderr and vm.calls() == []
