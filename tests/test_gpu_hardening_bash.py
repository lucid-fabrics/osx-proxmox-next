"""The installer's side of the audit fixes: every safety check the Python side has, run through bash."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gpu_fakes import add_card, add_other, make_host, write
from nullmoth_fakes import add_dev
from osx_proxmox_next import gpu
from test_gpu_parity import BASH, MAC, NV_STUBS, block_file, bash, nvidia_env, put_record, vm  # noqa: F401

MOVED = "The card at 0000:06:00 is now a different device. Your PCI layout changed. Run the scan again."
ENGINE = "GPU_ROOT"


def walkthrough_env(tmp_path, vm, **host):
    root = make_host(tmp_path / "root", **host)
    add_card(root)
    state_dir = root / "var/lib/osx-proxmox-next"
    env = {**vm.env, "GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_STATE_DIR": str(state_dir),
           "GPU_SNAPSHOT_BASE": str(tmp_path / "snap")}
    return root, state_dir, env


def stub_lspci(bash):
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\necho \"06:00.0 VGA compatible controller [0300]: Test Card [1002:73ff]\"\n")
    lspci.chmod(0o755)


def swap_in_nvme(root: Path) -> None:
    for fn in list((root / "sys/bus/pci/devices").glob("0000:06:00.*")):
        shutil.rmtree(fn)
        for link in (root / "sys/kernel/iommu_groups").glob(f"*/devices/{fn.name}"):
            link.unlink()
    add_other(root, "0000:06:00.0", 0x144D, 0xA80A, 0x010802, group=7, driver="nvme")


def chosen_state(bash, vm, tmp_path):
    root, state_dir, env = walkthrough_env(tmp_path, vm)
    stub_lspci(bash)
    bash("gpu_setup --yes --vm 100 --gpu 06:00", env=env, expect=None)  # stops at the BIOS or the host step
    state = json.loads((state_dir / "gpu-setup.json").read_text())
    assert state["gpu"]["identity"]["device"] == 0x73FF
    return root, state_dir, env, state


def test_the_choice_saves_the_cards_identity(bash, vm, tmp_path):
    _root, _dir, _env, state = chosen_state(bash, vm, tmp_path)
    assert state["gpu"]["identity"] == {"bdf": "0000:06:00", "vendor": 0x1002, "device": 0x73FF, "subsys_vendor": 0x1043,
                                        "subsys_device": 0x8800, "class": 0x030000}


@pytest.mark.parametrize("step", ["gpu_step_bios", "gpu_step_host", "gpu_step_verify", "gpu_step_vm no 100"])
def test_every_step_refuses_a_moved_card_and_sends_the_user_back_to_the_scan(bash, vm, tmp_path, step):
    root, state_dir, env, _state = chosen_state(bash, vm, tmp_path)
    swap_in_nvme(root)
    res = bash(f"{step}", env=env, expect=2)
    assert MOVED in res.stderr + res.stdout
    state = json.loads((state_dir / "gpu-setup.json").read_text())
    assert state["gpu"] is None and state["step"] == 1
    # nothing was planned or written for the NVMe
    assert not (root / "etc/modprobe.d/osx-proxmox-next-vfio.conf").exists()
    assert not (root / "etc/initramfs-tools/scripts/init-top/a-osx-proxmox-next-gpu-bind").exists()


def test_status_reports_a_moved_card_and_changes_nothing(bash, vm, tmp_path):
    root, state_dir, env, _state = chosen_state(bash, vm, tmp_path)
    swap_in_nvme(root)
    before = (state_dir / "gpu-setup.json").read_text()
    res = bash("gpu_setup --status", env=env, expect=2)
    assert MOVED in res.stdout and (state_dir / "gpu-setup.json").read_text() == before


def test_apply_through_the_engine_rechecks_the_identity(bash, vm, tmp_path):
    root, state_dir, env, _state = chosen_state(bash, vm, tmp_path)
    script = (
        'target=$(gpu_chosen_target); '
        'plan=$(python3 -c \'import json,sys; print(json.dumps({"target": json.loads(sys.argv[1]), "opts": {}}))\' "$target" | gpu_host plan); '
        'echo "$plan" > "$GPU_STATE_DIR/plan.json"')
    bash(script, env=env)
    plan = json.loads((state_dir / "plan.json").read_text())
    assert plan["changes"] and plan["identity"]["device"] == 0x73FF
    swap_in_nvme(root)
    res = bash('python3 -c \'import json,sys; print(json.dumps({"plan": json.load(open(sys.argv[1])), "confirmed": False}))\' '
               '"$GPU_STATE_DIR/plan.json" | gpu_host apply', env=env, expect=1)
    assert json.loads(res.stdout)["error"] == MOVED
    assert not (root / "etc/modprobe.d/osx-proxmox-next-vfio.conf").exists()


def test_the_raw_engine_refuses_a_stale_address_without_any_saved_identity(bash, vm, tmp_path):
    root, _dir, env = walkthrough_env(tmp_path, vm)
    swap_in_nvme(root)
    plan = bash('mkdir -p "$GPU_STATE_DIR"; gpu_target 0000:06:00 amd 29695 | python3 -c \'import json,sys; '
                'print(json.dumps({"target": json.load(sys.stdin), "opts": {}}))\' | gpu_host plan', env=env).stdout
    data = json.loads(plan)
    assert "not a AMD graphics card" in data["refusals"][0] and data["changes"] == []


# ── undo ─────────────────────────────────────────────────────────────

def test_undo_is_a_no_op_for_a_vm_that_is_gone_and_one_that_was_refused(bash, vm, tmp_path):
    root, state_dir, env = walkthrough_env(tmp_path, vm)
    state_dir.mkdir(parents=True)
    from osx_proxmox_next import host_setup
    (state_dir / "gpu-setup.json").write_text(json.dumps({**host_setup.new_state(), "vm_records": [100, 777, 800]}))
    put_record(tmp_path, 777, "0000:06:00", "amd", None, None, None, False, "")
    shutil.copy(tmp_path / "state/gpu-attach-777.json", state_dir / "gpu-attach-777.json")
    vm(800, "stopped", MAC)
    res = bash("gpu_setup --undo", env=env)
    assert "VM 777: it no longer exists" in res.stdout and "VM 800: nothing to revert" in res.stdout
    assert "VM 100: nothing to revert" in res.stdout or "VM 100" in res.stdout
    assert not (state_dir / "gpu-attach-777.json").exists() and not (state_dir / "gpu-setup.json").exists()
    assert vm.calls() == []


AFTER = {"hostpci0": "0000:06:00,pcie=1", "args": f'-device isa-applesmc,osk="x" -cpu host {gpu.HOTPLUG_ARG}', "vga": "none",
         "added_args": [gpu.HOTPLUG_ARG]}


def test_undo_keeps_what_the_user_changed_after_the_attach(bash, vm, tmp_path):
    put_record(tmp_path, 100, "0000:06:00", "amd", None, '-device isa-applesmc,osk="x" -cpu host', "std", True, "gpu", AFTER)
    edited = ('name: macos-sequoia\nargs: -device isa-applesmc,osk="x" -cpu host ' + gpu.HOTPLUG_ARG +
              " -smp 4\nhostpci0: 0000:06:00,pcie=1,x-vga=1\nvga: qxl\nide0: local-lvm:vm-100-disk-0,media=disk\n")
    vm(100, "stopped", edited)
    res = bash("gpu_oc_boot_args() { echo \"OC $*\"; }; gpu_detach_record 100", env=vm.env)
    assert vm.calls() == ['qm set 100 --args -device isa-applesmc,osk="x" -cpu host -smp 4']
    assert res.stdout.count("KEPT:") == 3 and "hostpci0 was changed" in res.stdout and "display setting" in res.stdout
    assert "OC 100 restore" in res.stdout


def test_undo_restores_normally_when_nothing_changed(bash, vm, tmp_path):
    put_record(tmp_path, 100, "0000:06:00", "amd", None, '-device isa-applesmc,osk="x" -cpu host', "std", True, "gpu", AFTER)
    vm(100, "stopped", 'name: macos-sequoia\nargs: ' + AFTER["args"] + "\nhostpci0: pcie=1,0000:06:00\nvga: none\n")
    bash("gpu_oc_boot_args() { true; }; gpu_detach_record 100", env=vm.env)
    assert vm.calls() == ["qm set 100 --delete hostpci0", 'qm set 100 --args -device isa-applesmc,osk="x" -cpu host', "qm set 100 --vga std"]


def test_undo_can_stop_a_running_vm_when_asked(bash, vm, tmp_path):
    put_record(tmp_path, 100, "0000:06:00", "amd", None, None, None, False, "")
    vm(100, "running", "name: x\nhostpci0: 0000:06:00,pcie=1\n")
    assert "Stop it first" in bash("gpu_detach_record 100", env=vm.env, expect=1).stdout
    bash("gpu_detach_record 100 no yes", env=vm.env)
    assert (Path(vm.env["QM_DIR"]) / "100.status").read_text().strip() == "stopped"
    assert "--stop-vm" in BASH[BASH.index("function nvidia_detach"):BASH.index("function nvidia_detach") + 400]
    assert 'gpu_undo "$([ "$stop" == "yes" ]' in BASH


def test_a_refused_nvidia_attach_leaves_a_record_that_undo_can_finish(bash, vm, tmp_path):
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    env = nvidia_env(tmp_path, vm.env)
    bash("nullmoth_fetch_amfipass() { echo /cache/a.zip; }; nullmoth_oc_patch() { return 1; }; nvidia_attach 100 01:00 --accept-nullmoth-risk",
         env=env, expect=1)
    assert (tmp_path / "state/gpu-attach-100.json").exists() and vm.calls() == []
    out = bash(NV_STUBS + "gpu_detach_record 100", env=env).stdout
    assert "NMOC 100 restore" in out and not (tmp_path / "state/gpu-attach-100.json").exists() and vm.calls() == []


def test_nvidia_attach_refuses_a_vm_with_its_recovery_disk_and_forgets_earlier_refusals(bash, vm, tmp_path):
    env = nvidia_env(tmp_path, vm.env)
    vm(100, "stopped", MAC.replace("sequoia", "tahoe") + "ide2: local:iso/rec.img,media=disk\n")
    vm(101, "stopped", MAC.replace("sequoia", "tahoe"))
    out = bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk || true; nvidia_attach 101 01:00 --dry-run",
               env=env, expect=0).stdout
    assert "recovery disk attached" in out and "DRY RUN" in out and out.count("REFUSED") == 1


# ── mappings, containers, --vm at step 7, acknowledgement ────────────

def test_a_card_held_through_a_mapping_is_in_use(bash, vm, tmp_path):
    root, _dir, env = walkthrough_env(tmp_path, vm)
    write(root, "/etc/pve/mapping/pci.cfg", "gpu-a\n\tmap id=1002:73ff,iommugroup=7,node=pve,path=0000:06:00.0\n")
    vm(120, "running", "hostpci0: mapping=gpu-a,pcie=1\n")
    vm(121, "stopped", "hostpci0: mapping=other\n")
    out = bash("gpu_users 100 0000:06:00", env=env).stdout
    assert out.strip() == "120 running"
    assert bash("gpu_users 100 0000:07:00", env=env).stdout.strip() == ""


def test_scan_names_the_containers_using_the_card(bash, vm, tmp_path):
    root, _dir, env = walkthrough_env(tmp_path, vm)
    write(root, "/etc/pve/lxc/105.conf", "dev0: /dev/dri/renderD128\n")
    stub_lspci(bash)
    out = bash('gpu_scan; for l in "${GPU_CARDS[@]}"; do echo "$l"; gpu_card_text "$l"; done', env=env).stdout
    assert out.splitlines()[0].endswith("|105:/dev/dri") and "container 105 uses /dev/dri" in out


def test_a_pipe_in_a_card_name_does_not_shift_the_fields(bash, vm, tmp_path):
    root, _dir, env = walkthrough_env(tmp_path, vm)
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\necho \"06:00.0 VGA compatible controller [0300]: Odd | Name Card [1002:73ff]\"\n")
    lspci.chmod(0o755)
    out = bash('gpu_scan; echo "${GPU_CARDS[0]}"; gpu_card_selectable "${GPU_CARDS[0]}" && echo SELECTABLE', env=env).stdout
    assert "Odd / Name Card|native|" in out and "SELECTABLE" in out


def test_step_7_uses_the_vm_given_on_the_command_line(bash, vm, tmp_path):
    root, state_dir, env, _state = chosen_state(bash, vm, tmp_path)
    gpu_state = json.loads((state_dir / "gpu-setup.json").read_text())
    gpu_state.update(vm=None, step=7)
    (state_dir / "gpu-setup.json").write_text(json.dumps(gpu_state))
    vm(101, "stopped", "name: plain\n")
    res = bash("gpu_step_vm no 101", env=env, expect=2)
    assert "VM 101 is not a macOS VM made by this tool" in res.stdout
    res = bash("GPU_YES=no; gpu_ui_yesno() { return 1; }; gpu_step_vm no 100", env=env, expect=0)
    assert json.loads((state_dir / "gpu-setup.json").read_text())["vm"] == 100 and "--vm <vmid>" not in res.stdout


def test_step_7_checks_the_nullmoth_acknowledgement_from_the_state(bash, vm, tmp_path):
    root = make_host(tmp_path / "root")
    add_card(root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    state_dir = root / "var/lib/osx-proxmox-next"
    env = {**vm.env, "GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_STATE_DIR": str(state_dir)}
    from osx_proxmox_next import host_setup
    ident = host_setup.identity_of(str(root), "0000:01:00")
    state_dir.mkdir(parents=True)
    state = {**host_setup.new_state(), "step": 7, "vm": 100, "ack_nullmoth": False,
             "gpu": {"bdf": "0000:01:00", "vendor": "nvidia", "device": 0x2504, "boot_vga": False, "name": "x", "identity": ident}}
    (state_dir / "gpu-setup.json").write_text(json.dumps(state))
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    res = bash(NV_STUBS + "gpu_step_vm no", env=env, expect=2)
    assert "notice has not been acknowledged" in res.stdout and vm.calls() == []


def test_the_bios_card_carries_the_qr_code_inside_the_frame(bash, vm, tmp_path):
    root, _dir, env = walkthrough_env(tmp_path, vm, virt=False, tables=(), efi=False)
    stub_lspci(bash)
    qr = bash.stub_bin / "qrencode"
    qr.write_text("#!/bin/sh\nprintf '%s\\n' '  ##  ' '##  ##' '  ##  '\n")
    qr.chmod(0o755)
    res = bash("gpu_setup --yes --vm 100 --gpu 06:00", env=env, expect=3)
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith("| ")]
    assert any("##  ##" in ln and ln.endswith("|") for ln in lines)
    assert "Without it no VM can start." in res.stdout


def test_the_bash_trap_exits_and_restores_the_callers_handler():
    for name in ("nullmoth_oc_patch", "gpu_oc_boot_args"):
        body = BASH[BASH.index(f"function {name}()"):]
        body = body[:body.index("\n}\n")]
        assert "exit 130" in body and "old_trap=$(trap -p INT TERM)" in body and 'eval "$old_trap"' in body


def test_the_amfipass_hash_is_passed_to_the_patcher():
    body = BASH[BASH.index("function nullmoth_oc_patch()"):]
    assert 'AMFIPASS_SHA256="${zip:+$NULLMOTH_AMFIPASS_SHA256}"' in body[:body.index("\n}\n")]


def test_the_bash_vfio_hint_matches_the_pythons_and_skips_storage(bash, tmp_path):
    root = make_host(tmp_path / "root")
    add_card(root)
    add_other(root, "0000:06:00.2", 0x144D, 0xA80A, 0x010802, group=7, driver="nvme")
    add_other(root, "0000:06:00.3", 0x1002, 0x4444, 0x020000, group=7, driver="e1000")
    out = bash(f'GPU_HINTS=(); gpu_vfio_hint "{root}/sys" 0000:06:00; printf "%s\\n" "${{GPU_HINTS[@]}}"').stdout.splitlines()
    assert out == gpu.vfio_conf_hint(root / "sys", "0000:06:00")
