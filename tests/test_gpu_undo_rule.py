"""Regression tests for the second security re-audit (NEW-1 to NEW-6): undo removes only what attach added."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gpu_fakes import FakeRun, add_card, make_host, write
from nullmoth_fakes import FakeQm, add_dev  # noqa: F401
from osx_proxmox_next import gpu, host_setup as hs
from test_gpu_hardening import GPU_OC, NM, fake_zip, oc_dir, run_patcher
from test_gpu_hardening_bash import chosen_state, swap_in_nvme  # noqa: F401
from test_gpu_parity import BASH, MAC, bash, block_file, vm  # noqa: F401

HOTPLUG = gpu.HOTPLUG_ARG
BASE_ARGS = '-device isa-applesmc,osk="x" -cpu host ' + HOTPLUG


def amd_record(cfg, vga_none=False):
    return hs.make_record(100, "0000:06:00", "amd", cfg, "0000:06:00,pcie=1", [HOTPLUG], vga_none, "gpu")


# ── NEW-1: the flag the VM was created with is never removed ──────────

def test_undo_after_an_args_edit_keeps_the_hotplug_flag_the_vm_was_created_with():
    cfg = {"args": BASE_ARGS, "vga": "std"}
    rec = amd_record(cfg)
    assert rec["after"]["added_args"] == [] and rec["after"]["replaced_args"] == {}
    edited = dict(cfg, args=BASE_ARGS.replace("-cpu host", "-cpu Skylake-Client"))
    res = hs.vm_restore(rec, edited)
    assert all(a["key"] != "args" for a in res["actions"])  # nothing of ours to remove
    assert "kept" in " ".join(res["notes"])


def test_undo_after_an_args_edit_still_removes_a_flag_the_attach_added():
    cfg = {"args": '-cpu host'}
    rec = amd_record(cfg)
    assert rec["after"]["added_args"] == [HOTPLUG]
    res = hs.vm_restore(rec, {"args": '-cpu Skylake-Client ' + HOTPLUG})
    assert res["actions"] == [{"op": "set", "key": "args", "value": "-cpu Skylake-Client"}]


def test_a_users_own_pci_hole_size_is_put_back_not_dropped():
    own = "-global q35-pcihost.pci-hole64-size=32G"
    cfg = {"args": "-cpu host " + own}
    rec = hs.make_record(100, "0000:01:00", "nvidia", cfg, "0000:01:00,pcie=0", [gpu.MMIO_ARG], False, "nullmoth")
    assert rec["after"]["replaced_args"] == {gpu.MMIO_ARG: own}
    res = hs.vm_restore(rec, {"args": "-cpu Skylake-Client " + gpu.MMIO_ARG})
    assert res["actions"] == [{"op": "set", "key": "args", "value": "-cpu Skylake-Client " + own}]


# ── NEW-2: a refused or partial attach leaves nothing to undo ─────────

@pytest.mark.parametrize("args", [BASE_ARGS, "-cpu host -global q35-pcihost.pci-hole64-size=32G", None])
def test_undo_of_an_attach_that_wrote_nothing_is_a_clean_no_op(args):
    cfg = {"vga": "std", **({"args": args} if args else {})}
    for vga_none, prof in ((True, [HOTPLUG]), (False, [gpu.MMIO_ARG])):
        rec = hs.make_record(100, "0000:06:00", "amd", cfg, "0000:06:00,pcie=1", prof, vga_none, "gpu")
        assert hs.vm_restore(rec, cfg) == {"actions": [], "notes": []}


def test_a_partial_attach_only_reverts_the_hostpci_it_wrote():
    cfg = {"args": "-cpu host -global q35-pcihost.pci-hole64-size=32G", "vga": "std"}
    rec = hs.make_record(100, "0000:01:00", "nvidia", cfg, "0000:01:00,pcie=0", [gpu.MMIO_ARG], True, "nullmoth")
    res = hs.vm_restore(rec, dict(cfg, hostpci0="0000:01:00,pcie=0"))
    assert res == {"actions": [{"op": "delete", "key": "hostpci0"}], "notes": []}


# ── NEW-3: the boot script also checks the subsystem ids ──────────────

def twin_plan(tmp_path, same_subsys=False):
    root = make_host(tmp_path)
    add_card(root, bdf="0000:06:00", group=7)
    add_card(root, bdf="0000:41:00", group=9)
    if not same_subsys:
        for fn in ("0000:41:00.0", "0000:41:00.1"):
            (root / "sys/bus/pci/devices" / fn / "subsystem_device").write_text("0x9999\n")
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), tgt, {}, str(root), FakeRun())
    return root, plan


def test_the_boot_script_skips_a_twin_that_took_over_the_address(tmp_path):
    root, plan = twin_plan(tmp_path)
    script = next(c["new"] for c in plan["changes"] if c["path"] == hs.BIND_SCRIPT)
    assert "bind 0000:06:00.0 0x1002 0x73ff 0x03 0x1043 0x8800" in script
    path = tmp_path / "bind.sh"
    path.write_text(script)
    # the cards swapped slots: the address now holds the twin with other subsystem ids
    for fn in ("0000:06:00.0", "0000:06:00.1"):
        (root / "sys/bus/pci/devices" / fn / "subsystem_device").write_text("0x9999\n")
    subprocess.run(["sh", str(path)], env={"OSX_NEXT_SYSFS": str(root / "sys"), "PATH": "/usr/bin:/bin"}, check=True)
    assert not (root / "sys/bus/pci/devices/0000:06:00.0/driver_override").exists()
    assert not (root / "sys/bus/pci/devices/0000:06:00.1/driver_override").exists()


def test_a_twin_with_identical_subsystem_ids_gets_a_do_not_move_warning(tmp_path):
    _root, plan = twin_plan(tmp_path, same_subsys=True)
    assert any("Do not move either card" in w and "0000:41:00" in w for w in plan["warnings"])
    _root, plan = twin_plan(tmp_path / "other")
    assert not any("Do not move" in w for w in plan["warnings"])


def test_an_old_bind_script_without_subsystem_ids_is_still_read(tmp_path):
    rows = hs._bind_entries("bind 0000:06:00.0 0x1002 0x73ff 0x03\n")
    assert rows == [("0000:06:00.0", "0x1002", "0x73ff", "0x03", "-", "-")]
    assert "bind 0000:06:00.0 0x1002 0x73ff 0x03 - -" in hs._bind_script(rows)


# ── NEW-4: a mapping with several paths ───────────────────────────────

def test_every_path_of_a_mapping_counts(tmp_path):
    write(tmp_path, "/etc/pve/mapping/pci.cfg",
          "gpu2\n\tmap id=1002:73ff,node=a,path=0000:07:00.0;0000:06:00.0\n\tmap id=1002:73ff,node=b,path=0000:08:00.0\n")
    maps = hs.load_mappings(str(tmp_path))
    assert maps == {"gpu2": {"0000:07:00", "0000:06:00", "0000:08:00"}}
    assert hs.cfg_holds_card({"hostpci0": "mapping=gpu2,pcie=1"}, "0000:06:00", maps)
    js = json.dumps([{"id": "gpu3", "map": ["id=1002:73ff,node=a,path=0000:07:00.0;0000:06:00.0"]}])
    got = hs.load_mappings(str(tmp_path / "none"), FakeRun({"pvesh get /cluster/mapping/pci --output-format json": (0, js)}))
    assert got == {"gpu3": {"0000:07:00", "0000:06:00"}}


# ── NEW-5: a kept OpenCore config does not lock the VM ────────────────

@pytest.mark.parametrize("script,env,backup", [(NM, True, "config.plist.pre-nullmoth"), (GPU_OC, False, "config.plist.pre-gpu")])
def test_a_kept_config_moves_the_backup_aside_and_later_attaches_work(tmp_path, script, env, backup):
    zip_env = {"AMFIPASS_ZIP": str(fake_zip(tmp_path))} if env else {"GPU_BOOT_ARGS": "agdpmod=pikera"}
    oc = oc_dir(tmp_path)
    original = (oc / "config.plist").read_bytes()
    assert run_patcher(script, oc, "attach", zip_env).returncode == 0
    mine = (oc / "config.plist").read_bytes() + b"<!-- edited -->"
    (oc / "config.plist").write_bytes(mine)
    res = run_patcher(script, oc, "restore")
    assert res.returncode == 0 and "KEPT:" in res.stderr and ".kept-" in res.stderr
    kept = list(oc.glob(backup + ".kept-*"))
    assert len(kept) == 1 and kept[0].read_bytes() == original
    assert not (oc / backup).exists() and not (oc / (backup + ".patched-sha256")).exists()
    assert (oc / "config.plist").read_bytes() == mine
    again = run_patcher(script, oc, "restore")  # nothing left to keep: no second KEPT
    assert again.returncode == 0 and "KEPT:" not in again.stderr
    # a new attach starts from the user's version and its own clean backup
    assert run_patcher(script, oc, "attach", zip_env).returncode == 0
    assert (oc / backup).read_bytes() == mine
    assert run_patcher(script, oc, "restore").returncode == 0 and (oc / "config.plist").read_bytes() == mine


def test_the_nvidia_setup_is_not_blocked_by_a_kept_amd_config(tmp_path):
    oc = oc_dir(tmp_path)
    assert run_patcher(GPU_OC, oc, "attach", {"GPU_BOOT_ARGS": "agdpmod=pikera"}).returncode == 0
    (oc / "config.plist").write_bytes((oc / "config.plist").read_bytes() + b"<!-- e -->")
    assert run_patcher(GPU_OC, oc, "restore").returncode == 0
    (oc / "config.plist.pre-nullmoth").write_text("x")
    assert run_patcher(GPU_OC, oc, "attach", {"GPU_BOOT_ARGS": "a=1"}).returncode == 2  # a live NullMoth backup still blocks


# ── bash: the same engine, and NEW-6 ──────────────────────────────────

def test_bash_record_does_not_claim_the_creation_time_hotplug_flag(bash, vm, tmp_path):
    vm(100, "stopped", MAC.replace("-cpu host", "-cpu host " + HOTPLUG))
    bash('gpu_record_write 100 0000:06:00 amd "0000:06:00,pcie=1" "$GPU_HOTPLUG_ARG" no gpu', env=vm.env)
    rec = json.loads((tmp_path / "state/gpu-attach-100.json").read_text())
    assert rec["after"]["added_args"] == [] and rec["after"]["replaced_args"] == {}


def test_bash_undo_after_a_kept_file_does_not_claim_everything_is_back(bash, tmp_path):
    stub = ('gpu_host() { case "$1" in undo) echo \'{"ok": true, "restored": ["/etc/a"], '
            '"kept": ["/etc/modprobe.d/vfio.conf was changed after this tool wrote it, so your version was kept."], '
            '"errors": [], "reboot_required": false}\' ;; esac; }; ')
    out = bash(stub + "gpu_undo no").stdout
    assert "Restored /etc/a" in out and "your version was kept" in out
    assert "Everything this setup changed is back" not in out and "listed as kept above" in out
    clean = bash(stub.replace('"kept": ["/etc/modprobe.d/vfio.conf was changed after this tool wrote it, so your version was kept."]',
                              '"kept": []') + "gpu_undo no").stdout
    assert "Everything this setup changed is back as it was." in clean


def test_bash_undo_notes_a_card_that_changed_like_python(bash, vm, tmp_path):
    root, state_dir, env, _state = chosen_state(bash, vm, tmp_path)
    swap_in_nvme(root)
    out = bash("gpu_undo no", env=env, expect=None).stdout
    assert "Note: the card at 0000:06:00 is now a different device" in out
    assert "nothing there is touched" in out
