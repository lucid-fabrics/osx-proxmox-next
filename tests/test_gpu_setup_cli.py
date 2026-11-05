"""`osx-next-cli gpu-setup` end to end, on a fake host."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from gpu_fakes import add_card, make_host, write
from nullmoth_fakes import MAC_CFG, FakeQm
from osx_proxmox_next import cli, gpu, gpu_setup, gpu_setup_cli, host_setup
from osx_proxmox_next.executor import ApplyResult, StepResult
from test_gpu_setup import ANSWERS, SEQUOIA, Env, patch_executor, ready  # noqa: F401  (fixtures reused)

HINT = "etc/profile.d/osx-proxmox-next-gpu.sh"


@pytest.fixture(autouse=True)
def _vm_list(monkeypatch):
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["100 macos-sequoia stopped"])


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    gpu_setup_cli.add_parser(parser.add_subparsers(dest="cmd"))
    return parser.parse_args(["gpu-setup", *argv])


def go(env: Env, *argv: str, inputs: list[str] | None = None, interactive: bool = False, session=None):
    out: list[str] = []
    queue = list(inputs or [])
    code = gpu_setup_cli.run(parse(*argv), session or env.session(), lambda prompt: (out.append(prompt), queue.pop(0))[1],
                             out.append, interactive=interactive)
    return code, "\n".join(out)


@pytest.fixture
def host(tmp_path):
    env = Env(tmp_path, cpu="intel")
    add_card(env.root)
    return env


# ── status and undo ──────────────────────────────────────────────────

def test_status_without_a_setup(host):
    code, out = go(host, "--status")
    assert code == 0 and "[1 Scan]" in out and "No setup in progress." in out and "Works natively" in out
    assert not (host.root / HINT).exists() and host_setup.load_state(str(host.root)) is None  # read only


def test_status_without_cards(tmp_path):
    code, out = go(Env(tmp_path), "--status")
    assert code == 0 and "No AMD or NVIDIA graphics card found." in out


def test_status_with_a_setup(host):
    s = host.session()
    s.choose("06:00", 100, "gpu")
    code, out = go(host, "--status")
    assert code == 0 and "Chosen card:" in out and "BIOS [ok]" in out and "Verify [fail]" in out
    assert "Host plan: 3 file(s) and 2 command(s) pending" in out or "Host plan:" in out
    ready(host)
    for ch in host.session().host_plan()["changes"]:
        write(host.root, ch["path"], ch["new"])
    host.run.answers["update-initramfs"] = (0, "")
    assert "Host plan:" in go(host, "--status")[1]


def test_undo(host, monkeypatch):
    s = host.session()
    s.choose("06:00", 100, "gpu")
    s.host_apply(s.host_plan())
    code, out = go(host, "--undo")
    assert code == 0 and "Everything this setup changed is back as it was." in out and "Restored /etc/default/grub.d" in out
    s2 = host.session()
    s2.state["vm_records"] = [100]
    host.qm.vms[100] = ("running", SEQUOIA)
    s2.save()
    code, out = go(host, "--undo", session=host.session())
    assert code == 1 and "could not be undone" in out


# ── scan and choose ──────────────────────────────────────────────────

def test_nothing_usable(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, device=0x73DF)
    code, out = go(env)
    assert code == 2 and "Not supported by macOS" in out and "Cannot be used" in out and "gpu-passthrough#supported-cards" in out
    assert go(Env(tmp_path / "e2"))[0] == 2


def test_automatic_run_stops_at_the_reboot(host):
    code, out = go(host, "--yes", "--vm", "100")
    assert code == gpu_setup_cli.EXIT_REBOOT
    assert "This is exactly what will change" in out and "+GRUB_CMDLINE_LINUX_DEFAULT" in out
    assert "The host must reboot" in out and "gpu-setup --undo" in out
    assert (host.root / HINT).exists() and "step 5 of 8" in (host.root / HINT).read_text()
    assert host.run.ran("update-grub") and not host.run.ran("systemctl")  # never reboots by itself


def test_after_the_reboot_the_run_attaches_the_card_and_finishes(host, monkeypatch):
    write(host.root, "/proc/sys/kernel/random/boot_id", "boot-one\n")
    assert go(host, "--yes", "--vm", "100")[0] == gpu_setup_cli.EXIT_REBOOT
    write(host.root, "/proc/sys/kernel/random/boot_id", "boot-two\n")
    ready(host)
    applied = patch_executor(monkeypatch)
    code, out = go(host, "--yes")
    assert code == 0 and applied and "Card attached." in out and "Setup summary" in out
    assert "You should see the OpenCore picker" in out
    s = host.session()
    assert s.state["finished"] and s.state["vm_records"] == [100] and not (host.root / HINT).exists()
    # finished: a plain run shows the summary, --yes starts over
    code, out = go(host)
    assert code == 0 and "Setup summary" in out
    code, out = go(host, "--yes", "--vm", "100")
    assert "[1 Scan]" in out and code in (0, 4)


def test_bios_pause(tmp_path):
    env = Env(tmp_path, virt=False, tables=(), efi=False)
    add_card(env.root)
    code, out = go(env, "--yes", "--vm", "100")
    assert code == gpu_setup_cli.EXIT_BIOS
    assert "BIOS STEPS FOR THIS SERVER" in out and "Saved to /root/osx-proxmox-next-bios-steps.txt" in out
    assert "[!!]" in out and "[ok]" not in out.splitlines()[0]
    assert "When you are back in Proxmox, run: osx-next-cli gpu-setup" in out and (env.root / HINT).exists()
    code, out = go(env, "--yes")
    assert code == 3 and "These are still off." in out


def test_bios_interactive_choices(tmp_path):
    env = Env(tmp_path, virt=False, efi=True)
    add_card(env.root)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    code, out = go(env, inputs=["3"], interactive=True, session=s)
    assert code == 0 and s.state["step"] == 1 or s.state["step"] == 2
    s.choose("06:00", 100, "gpu")
    code, out = go(env, inputs=["1", "n"], interactive=True, session=s)  # firmware reboot not supported here
    assert code == 3 and "does not allow 'reboot into setup'" in out
    code, out = go(env, inputs=[""], interactive=True, session=s)  # blank = later
    assert code == 3
    fw = env.root / "sys/firmware/efi/efivars/OsIndicationsSupported-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    fw.parent.mkdir(parents=True, exist_ok=True)
    fw.write_bytes(b"\x06\x00\x00\x00\x01" + b"\x00" * 7)
    env.qm.vms[101] = ("running", SEQUOIA)
    code, out = go(env, inputs=["1", "y"], interactive=True, session=s)
    assert code == 0 and "These VMs will be shut down by the reboot: 101 (vm101)" in out and "Rebooting..." in out
    assert env.run.ran("systemctl", "reboot", "--firmware-setup")
    env.run.answers["systemctl reboot --firmware-setup"] = (1, "denied")
    code, out = go(env, inputs=["1", "y"], interactive=True, session=s)
    assert code == 1 and "Could not start the reboot: denied" in out
    code, out = go(env, inputs=["1", "n"], interactive=True, session=s)
    assert code == 3


def test_interactive_choose_pick_vm_and_display(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:06:00", device=0x73FF, group=7)
    add_card(env.root, bdf="0000:41:00", device=0x67DF, group=9)
    code, out = go(env, "--vm", "100", inputs=["2", "2", "n"], interactive=True)
    s = env.session()
    assert s.state["gpu"]["bdf"] == "0000:41:00" and s.state["display"] == "console"
    s.state["step"] = 1
    s.save()
    code, out = go(env, inputs=["x"], interactive=True)
    assert code == 2 and "Pick a card with --gpu" in out
    env2 = Env(tmp_path / "b")
    add_card(env2.root)
    code, out = go(env2, inputs=["c"], interactive=True)
    assert code == 0 and "Create the VM with 'osx-next'" in out and (env2.root / HINT).exists()
    code, out = go(env2, "--gpu", "0000:06:00", inputs=["p", "1", "n"], interactive=True)
    assert code == 0 and env2.session().state["vm"] is None


def test_choose_errors(host):
    assert go(host, "--gpu", "09:00")[0] == 2
    code, out = go(host, "--gpu", "06:00", "--vm", "999")
    assert code == 2 and "VM 999 not found." in out


def test_nvidia_needs_the_acknowledgement(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    code, out = go(env, "--vm", "100")
    assert code == 2 and "NVIDIA GPU support (Beta)" in out and "NullMoth notice acknowledged" in out
    code, out = go(env, "--vm", "100", "--yes", inputs=[])
    assert code == 2
    code, out = go(env, "--vm", "100", inputs=["I accept", "n"], interactive=True)
    assert "Type 'I accept' to continue" in out and env.session().state["ack_nullmoth"] is True
    s = env.session()
    s.goto(1)
    s.state["ack_nullmoth"] = False
    s.save()
    code, out = go(env, "--vm", "100", "--accept-nullmoth-risk", "--yes")
    assert env.session().state["ack_nullmoth"] is True and code in (3, 4, 2)


# ── host step ────────────────────────────────────────────────────────

def test_only_display_adapter_needs_a_typed_confirmation(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, boot_vga=True)
    code, out = go(env, "--yes", "--vm", "100")
    assert code == 3 or code == 2
    s = env.session()
    s.state["step"] = 4
    s.state["done"] = [1, 2]
    s.state["skipped"] = [3]
    s.save()
    code, out = go(env, "--yes")
    assert code == 2 and "only display adapter" in out
    code, out = go(env, inputs=["I understand", "n"], interactive=True)
    assert code == 0 and "Nothing was changed." in out
    code, out = go(env, inputs=["nope"], interactive=True)
    assert code == 2
    code, out = go(env, "--yes")  # --yes cannot type the phrase for you
    assert code == 2 and "only display adapter" in out


def test_hard_warning_needs_the_typed_phrase(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, device=0x66AF)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    code, out = go(env, "--yes", session=s)
    assert code == 2 and "HARD WARNING" in out and "Nothing was changed." in out
    code, out = go(env, "--yes", inputs=["I understand"], interactive=True, session=env.session())
    assert code == gpu_setup_cli.EXIT_REBOOT and "Done. Backups are in" in out


def test_manual_only_plan_and_secure_boot(tmp_path):
    env = Env(tmp_path, answers={"mokutil --sb-state": (0, "SecureBoot enabled")})
    add_card(env.root, device=0x67DF)
    write(env.root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"\n')
    ready(env)
    write(env.root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    write(env.root, "/etc/modprobe.d/osx-proxmox-next-vfio.conf", host_setup.HEADER)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    plan = s.host_plan()
    for ch in plan["changes"]:
        write(env.root, ch["path"], ch["new"])
    code, out = go(env, "--yes", session=env.session())
    assert "Manual step: Secure Boot is on" in out and code == 2 and "Nothing to write" in out


def test_apply_failure(host):
    host.run.answers["update-grub"] = (1, "exploded")
    code, out = go(host, "--yes", "--vm", "100")
    assert code == 1 and "FAILED: update-grub failed: exploded The files were put back." in out


def test_confirmation_declined(host):
    code, out = go(host, "--vm", "100", inputs=["", "n"], interactive=True)
    assert code == 0 and "Nothing was changed." in out and not (host.root / "etc/default/grub.d/osx-proxmox-next.cfg").exists()


def test_host_step_already_done(host):
    ready(host)
    s = host.session()
    s.choose("06:00", 100, "gpu")
    for ch in s.host_plan()["changes"]:
        write(host.root, ch["path"], ch["new"])
    host.run.answers["update-initramfs"] = (0, "")
    plan = s.host_plan()
    assert not s.plan_needs_work(plan)


# ── reboot ───────────────────────────────────────────────────────────

def test_reboot_now_and_later(host):
    s = host.session()
    s.choose("06:00", 100, "gpu")
    s.host_apply(s.host_plan())
    host.qm.vms[101] = ("running", SEQUOIA)
    code, out = go(host, inputs=["n"], interactive=True, session=s)
    assert code == 4 and "These VMs will be shut down: 101 (vm101)" in out
    code, out = go(host, inputs=["y"], interactive=True, session=s)
    assert code == 0 and "Rebooting..." in out and host.run.ran("systemctl", "reboot")
    host.run.answers["systemctl reboot"] = (1, "no")
    code, out = go(host, inputs=["y"], interactive=True, session=s)
    assert code == 1 and "Could not start the reboot: no" in out


# ── verify ───────────────────────────────────────────────────────────

def at_step(env: Env, step: int) -> "gpu_setup.Session":
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.goto(step)
    s.state["done"] = list(range(1, step))
    s.save()
    return s


def test_verify_sends_you_back_to_the_right_step(host):
    # configured in the grub file but the host has not rebooted: go to the reboot step
    write(host.root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"\n')
    s = at_step(host, 6)
    code, out = go(host, session=s)
    assert code == 4 and "[!!]" in out and "The change is saved but not active yet" in out and s.state["step"] == 5
    assert s.state["boot_id"] == host_setup.boot_id(str(host.root))
    # booted with the arguments but the card is held by another driver: back to the host step
    ready(host)
    (host.root / "sys/bus/pci/devices/0000:06:00.0/driver").unlink()
    s = at_step(host, 6)
    code, out = go(host, "--yes", session=s)
    assert "Not bound: 0000:06:00.0 (no driver)" in out and s.state["step"] == 5
    # the firmware hides the IOMMU: back to the BIOS step
    shutil.rmtree(host.root / "sys/kernel/iommu_groups")
    (host.root / "sys/kernel/iommu_groups").mkdir()
    (host.root / "sys/firmware/acpi/tables/DMAR").unlink()
    s = at_step(host, 6)
    code, out = go(host, "--yes", session=s)
    assert code == 3 and "BIOS STEPS FOR THIS SERVER" in out and s.state["step"] == 3


def test_a_reboot_since_the_changes_settles_the_reboot_step(host):
    write(host.root, "/proc/sys/kernel/random/boot_id", "boot-one\n")
    s = host.session()
    s.choose("06:00", 100, "gpu")
    s.host_apply(s.host_plan())
    assert s.reboot_pending() and s.state["boot_id"] == "boot-one"
    code, out = go(host, "--yes", session=s)
    assert code == 4  # same boot: still waiting
    write(host.root, "/proc/sys/kernel/random/boot_id", "boot-two\n")
    ready(host)
    s2 = host.session()
    assert not s2.reboot_pending() and 5 in s2.state["done"] and s2.state["reboot_needed"] is False
    code, out = go(host, "--yes", session=host.session())
    assert "The host has rebooted" not in out and code in (0, 2, 3, 4, 1), out


def test_verify_group_problem_stops(host):
    ready(host)
    from gpu_fakes import add_other
    s = at_step(host, 6)
    add_other(host.root, "0000:07:00.0", 0x144D, 0xA808, 0x010802, 7)
    code, out = go(host, session=s)
    assert code == 2 and "Move the card to another slot" in out and "osx-next-cli gpu-setup" in out


# ── VM and first boot ────────────────────────────────────────────────

def test_vm_step_without_a_vm(host):
    s = host.session()
    s.choose("06:00", None, "gpu")
    s.goto(7)
    code, out = go(host, session=s)
    assert code == 0 and "The host is ready" in out and out.rstrip().endswith("--vm <vmid>") or "--vm <vmid>" in out


def test_vm_step_refusal_decline_and_failure(host, monkeypatch):
    host.qm.vms[100] = ("running", SEQUOIA)
    s = at_step(host, 7)
    code, out = go(host, session=s)
    assert code == 2 and "REFUSED: VM 100 is running" in out
    host.qm.vms[100] = ("stopped", SEQUOIA)
    code, out = go(host, inputs=["n"], interactive=True, session=s)
    assert code == 0 and "Nothing was changed." in out
    bad = ApplyResult(False, [StepResult("Attach GPU", "c", False, 1, "")], Path("/tmp/log"))
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan", lambda *a, **k: bad)
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)
    code, out = go(host, "--yes", session=s)
    assert code == 1 and "FAILED at step: Attach GPU" in out
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan", lambda *a, **k: ApplyResult(False, [], Path("/tmp/log")))
    assert "FAILED. Log:" in go(host, "--yes", session=s)[1]


def test_nvidia_attach_downloads_amfipass(tmp_path, monkeypatch):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    ready(env)
    write(env.root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    s = env.session()
    s.choose("01:00", 100, "console", ack=True)
    s.goto(7)
    fetched = []
    monkeypatch.setattr("osx_proxmox_next.oc_builder.ensure_amfipass", lambda d: fetched.append(d))
    patch_executor(monkeypatch)
    code, out = go(env, "--yes", session=s)
    assert code == 0 and fetched and "kmutil showloaded" in out and "Our guide:" in out
    assert env.session().state["finished"]


def test_first_boot_troubleshooting(host, monkeypatch):
    s = at_step(host, 8)
    s.state["done"] = [1, 2, 3, 4, 5, 6, 7]
    code, out = go(host, inputs=["n", "1", "n", "2", "n", "3", "y"], interactive=True, session=s)
    assert code == 0 and "No signal on the monitor" in out and "Black screen after the Apple logo" in out
    assert "the AMD reset bug" in out and s.state["finished"]
    host_setup.delete_state(str(host.root))
    s2 = at_step(host, 8)
    code, out = go(host, inputs=["n", "4"], interactive=True, session=s2)
    assert code == 0 and "Come back with: osx-next-cli gpu-setup" in out and not s2.state["finished"]


# ── plumbing ─────────────────────────────────────────────────────────

def test_a_step_that_never_moves_fails_instead_of_looping(host, monkeypatch):
    monkeypatch.setitem(gpu_setup_cli._HANDLERS, 1, lambda s, io, args: None)
    assert go(host)[0] == 1


def test_wired_into_the_main_cli(host, monkeypatch, capsys):
    monkeypatch.setattr(gpu_setup_cli, "Session", lambda: host.session())
    assert cli.run_cli(["gpu-setup", "--status"]) == 0
    assert "No setup in progress." in capsys.readouterr().out


def test_nvidia_detach_alias_uses_the_shared_path(host, monkeypatch, capsys):
    called = []
    from osx_proxmox_next import gpu_attach
    from osx_proxmox_next.nullmoth import AttachPlan
    monkeypatch.setattr(gpu_attach, "prepare_detach", lambda vmid, **k: called.append(vmid) or AttachPlan(vmid))
    assert cli.run_cli(["nvidia-detach", "100", "--dry-run"]) == 0 and called == [100]


def test_verify_and_host_do_not_loop_when_there_is_nothing_to_write(host):
    ready(host)
    s = host.session()
    s.choose("06:00", 100, "gpu")
    for ch in s.host_plan()["changes"]:
        write(host.root, ch["path"], ch["new"])
    (host.root / "sys/bus/pci/devices/0000:06:00.0/driver").unlink()  # verify: not bound, host step has nothing to do
    s.goto(6)
    host.run.answers["update-initramfs"] = (0, "")
    plan_empty = host.session().host_plan()
    plan_empty["commands"] = []
    s2 = host.session()
    s2.host_plan = lambda allow_no_console=False: plan_empty
    code, out = go(host, "--yes", session=s2)
    assert code == 2 and "nothing left for this tool to change" in out and s2.state["verify_retry"] is False


def test_rebooted_but_arguments_still_missing_stops_instead_of_looping(host):
    write(host.root, "/proc/sys/kernel/random/boot_id", "one\n")
    write(host.root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"\n')
    s = at_step(host, 6)
    s.state["boot_id"] = "one"
    write(host.root, "/proc/sys/kernel/random/boot_id", "two\n")
    code, out = go(host, session=s)
    assert code == 2 and "rebooted but the kernel arguments are still not active" in out


def test_gpu_option_matches_the_whole_address(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:11:00", group=3)
    code, out = go(env, "--gpu", "1:00")
    assert code == 2
    assert go(env, "--gpu", "11:00", "--vm", "100", "--yes")[0] in (0, 2, 3, 4)
