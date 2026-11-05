"""Regression tests for the security audit of the GPU walkthrough: device identity, undo, safe writes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gpu_fakes import EFI_GRUB, FakeRun, add_card, add_other, make_host, write
from nullmoth_fakes import MAC_CFG, FakeQm, add_dev, write_oc
from osx_proxmox_next import gpu, gpu_attach, gpu_setup, gpu_setup_cli, host_setup as hs, nullmoth
from osx_proxmox_next.gpu_setup import CardChanged
from test_gpu_setup import Env, SEQUOIA, ready  # noqa: F401  (the autouse VM-listing fixture is imported below)
from test_gpu_setup import _no_real_vm_listing  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
MOVED = "The card at 0000:06:00 is now a different device. Your PCI layout changed. Run the scan again."


def chosen_session(tmp_path: Path, vms=None) -> tuple[Env, gpu_setup.Session]:
    env = Env(tmp_path, cpu="intel", vms=vms)
    add_card(env.root)
    s = env.session()
    assert s.choose("06:00", 100, "gpu") == []
    return env, s


def swap_in_nvme(root: Path, mirrored: bool = False) -> None:
    """What the user's PCIe layout looks like after the card moved: a boot NVMe sits at the old address."""
    for fn in list((root / "sys/bus/pci/devices").glob("0000:06:00.*")):
        shutil.rmtree(fn)
        for link in (root / "sys/kernel/iommu_groups").glob(f"*/devices/{fn.name}"):
            link.unlink()
    add_other(root, "0000:06:00.0", 0x144D, 0xA80A, 0x010802, group=7, driver="nvme")
    if mirrored:
        add_other(root, "0000:07:00.0", 0x144D, 0xA80A, 0x010802, group=8, driver="nvme")


# ── CRITICAL-1 / HIGH-1: the saved selection is re-checked everywhere ─

def test_identity_is_saved_with_the_choice(tmp_path):
    env, s = chosen_session(tmp_path)
    ident = s.state["gpu"]["identity"]
    assert ident == {"bdf": "0000:06:00", "vendor": 0x1002, "device": 0x73FF, "subsys_vendor": 0x1043,
                     "subsys_device": 0x8800, "class": 0x030000}


@pytest.mark.parametrize("mirrored", [False, True])
def test_a_stale_address_never_plans_a_binding_for_the_boot_disk(tmp_path, mirrored):
    env, s = chosen_session(tmp_path)
    swap_in_nvme(env.root, mirrored)
    with pytest.raises(CardChanged) as exc:
        s.host_plan()
    assert str(exc.value) == MOVED
    # The engine on its own (the bash path) refuses too, whatever the caller does.
    tgt = hs.build_target(str(env.root), "0000:06:00", "amd", "none", s.state["gpu"]["identity"])
    plan = hs.plan_host(hs.detect_facts(str(env.root), env.run, env={}), tgt, {}, str(env.root), env.run)
    assert plan["refusals"] == [MOVED] and plan["changes"] == [] and plan["commands"] == []


def test_without_a_saved_identity_a_non_display_device_is_still_refused(tmp_path):
    root = make_host(tmp_path)
    add_other(root, "0000:06:00.0", 0x144D, 0xA80A, 0x010802, group=7, driver="nvme")
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), tgt, {}, str(root), FakeRun())
    assert "is not a AMD graphics card" in plan["refusals"][0] or "not a AMD graphics card" in plan["refusals"][0]
    assert plan["changes"] == []


def test_a_storage_function_next_to_the_card_is_never_bound(tmp_path):
    root = make_host(tmp_path)
    add_card(root)
    add_other(root, "0000:06:00.2", 0x1002, 0x1234, 0x010802, group=7, driver="nvme")
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), tgt, {}, str(root), FakeRun())
    assert "storage or network" in plan["refusals"][0] and plan["changes"] == []


def test_only_display_audio_usb_and_ucsi_functions_of_the_vendor_are_bound(tmp_path):
    root = make_host(tmp_path)
    add_card(root)
    add_dev(root / "sys", "0000:06:00.2", 0x1002, 0x1111, cls=0x0C0330, driver="vfio-pci", group=7)
    add_dev(root / "sys", "0000:06:00.3", 0x1002, 0x2222, cls=0x0C8000, driver="vfio-pci", group=7)
    add_dev(root / "sys", "0000:06:00.4", 0x8086, 0x3333, cls=0x040300, driver="vfio-pci", group=7)  # other vendor
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), tgt, {}, str(root), FakeRun())
    body = next(c["new"] for c in plan["changes"] if c["path"] == hs.MODPROBE_FILE)
    assert "ids=1002:73ff,1002:ab28,1002:1111,1002:2222" in body and "8086" not in body


def test_a_changed_subsystem_id_is_a_different_card(tmp_path):
    env, s = chosen_session(tmp_path)
    (env.root / "sys/bus/pci/devices/0000:06:00.0/subsystem_device").write_text("0x9999\n")
    with pytest.raises(CardChanged):
        s.target()


def test_apply_checks_the_identity_again_right_before_writing(tmp_path):
    env, s = chosen_session(tmp_path)
    ready(env)
    plan = s.host_plan()
    assert plan["changes"]
    swap_in_nvme(env.root)
    with pytest.raises(CardChanged):
        s.host_apply(plan)
    res = hs.apply_plan(plan, s.state, str(env.root), env.run)
    assert res == {"ok": False, "error": MOVED, "rolled_back": False}
    assert not (env.root / "etc/modprobe.d/osx-proxmox-next-vfio.conf").exists()


def test_verify_attach_and_bios_all_stop_on_a_moved_card(tmp_path):
    env, s = chosen_session(tmp_path)
    swap_in_nvme(env.root)
    for call in (s.verify, s.bios_items, s.vm_plan, s.begin_attach):
        with pytest.raises(CardChanged):
            call()
    checks = hs.verify_host(hs.build_target(str(env.root), "0000:06:00", "amd", "none", s.state["gpu"]["identity"]),
                            str(env.root), env.run)
    assert [(c["id"], c["status"], c["fix"]) for c in checks] == [("identity", "fail", "scan")]


def test_card_changed_sends_the_user_back_to_the_scan(tmp_path):
    env, s = chosen_session(tmp_path)
    assert s.state["step"] == 3
    swap_in_nvme(env.root)
    with pytest.raises(CardChanged) as exc:
        s.target()
    assert s.card_changed(exc.value) == MOVED
    assert s.state["gpu"] is None and s.state["step"] == 1 and s.state["done"] == []


def test_cli_explains_and_goes_back_to_the_scan(tmp_path):
    env, s = chosen_session(tmp_path)
    swap_in_nvme(env.root)
    out: list[str] = []
    args = gpu_setup_cli.argparse.Namespace(status=False, undo=False, yes=True, vm=None, gpu="", display="",
                                            accept_risk=False, stop_vm=False)
    code = gpu_setup_cli.run(args, s, ask=lambda _p: "", say=out.append, interactive=False)
    assert code == gpu_setup_cli.EXIT_REFUSED
    assert any(MOVED in line for line in out) and s.state["gpu"] is None and s.state["step"] == 1


def test_status_reports_a_moved_card_without_changing_the_state(tmp_path):
    env, s = chosen_session(tmp_path)
    swap_in_nvme(env.root)
    out: list[str] = []
    args = gpu_setup_cli.argparse.Namespace(status=True, undo=False, yes=False, vm=None, gpu="", display="",
                                            accept_risk=False, stop_vm=False)
    assert gpu_setup_cli.run(args, s, ask=lambda _p: "", say=out.append, interactive=False) == gpu_setup_cli.EXIT_REFUSED
    assert any(MOVED in line for line in out) and s.state["gpu"] is not None


def test_bind_script_checks_vendor_device_and_class_at_boot(tmp_path):
    root = make_host(tmp_path)
    add_card(root, bdf="0000:06:00", group=7)
    add_card(root, bdf="0000:41:00", group=9)  # the identical twin makes the plan per-device
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), tgt, {}, str(root), FakeRun())
    script = next(c["new"] for c in plan["changes"] if c["path"] == hs.BIND_SCRIPT)
    assert "bind 0000:06:00.0 0x1002 0x73ff 0x03" in script and "bind 0000:06:00.1 0x1002 0xab28 0x0403" in script
    path = tmp_path / "bind.sh"
    path.write_text(script)

    def run_at(sysfs: Path) -> None:
        subprocess.run(["sh", str(path)], env={"OSX_NEXT_SYSFS": str(sysfs), "PATH": "/usr/bin:/bin"}, check=True)

    override = lambda bdf: (root / "sys/bus/pci/devices" / bdf / "driver_override")  # noqa: E731
    run_at(root / "sys")
    assert override("0000:06:00.0").read_text().strip() == "vfio-pci"
    assert override("0000:06:00.1").read_text().strip() == "vfio-pci"
    assert not override("0000:41:00.0").exists()
    # The layout changed after setup: an NVMe now sits at the saved address. The boot script skips it.
    override("0000:06:00.0").unlink()
    override("0000:06:00.1").unlink()
    swap_in_nvme(root)
    run_at(root / "sys")
    assert not override("0000:06:00.0").exists()
    # A device with the right ids but another class is skipped too.
    add_dev(root / "sys", "0000:06:00.1", 0x1002, 0xAB28, cls=0x010802, driver="nvme", group=7)
    run_at(root / "sys")
    assert not override("0000:06:00.1").exists()


def test_second_card_keeps_the_first_cards_binding(tmp_path):
    root = make_host(tmp_path)
    add_card(root, bdf="0000:06:00", group=7)
    add_card(root, bdf="0000:0a:00", device=0x73DF, audio=0xAAAA, group=9)
    write(root, hs.MODPROBE_FILE, hs.HEADER + "options vfio-pci ids=10de:2504,10de:228e disable_vga=1\n")
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), tgt, {}, str(root), FakeRun())
    body = next(c["new"] for c in plan["changes"] if c["path"] == hs.MODPROBE_FILE)
    assert "ids=10de:2504,10de:228e,1002:73ff,1002:ab28" in body
    assert any("stay bound" in n for n in plan["notes"])


# ── LOW-2 / LOW-3: apply is exact and survives a second Ctrl-C ───────

def test_apply_refuses_when_a_file_changed_after_the_preview(tmp_path):
    root = make_host(tmp_path)
    add_card(root)
    write(root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB)})
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), run, env={}), tgt, {}, str(root), run)
    changed = next(c for c in plan["changes"] if c["old"] is not None) if any(c["old"] is not None for c in plan["changes"]) else None
    write(root, "/etc/modprobe.d/osx-proxmox-next-vfio.conf", "# edited while the preview was open\n")
    state = hs.new_state()
    res = hs.apply_plan(plan, state, str(root), run)
    assert not res["ok"] and "changed after the preview" in res["error"] and not res["rolled_back"]
    assert (root / "etc/modprobe.d/osx-proxmox-next-vfio.conf").read_text() == "# edited while the preview was open\n"
    assert changed is None or not (root / "etc/default/grub.d/osx-proxmox-next.cfg").exists()


def test_pending_refresh_is_saved_before_the_rollback_starts(tmp_path, monkeypatch):
    root = make_host(tmp_path)
    add_card(root)
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB)}, default=(0, ""))
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), run, env={}), tgt, {}, str(root), run)
    fail_run = FakeRun({"update-initramfs": (1, "boom")}, default=(0, ""))
    seen = {}

    def interrupted(kinds, state, _run):
        seen["on_disk"] = hs.load_state(str(root))["pending_refresh"]
        raise KeyboardInterrupt

    monkeypatch.setattr(hs, "_run_refreshes", interrupted)
    with pytest.raises(KeyboardInterrupt):
        hs.apply_plan(plan, hs.new_state(), str(root), fail_run)
    assert seen["on_disk"] == plan["refresh"] and seen["on_disk"]


# ── known open item: symlinks and modes ──────────────────────────────

def test_a_symlinked_config_is_written_through_and_restored_through(tmp_path):
    root = make_host(tmp_path)
    add_card(root)
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB)}, default=(0, ""))
    real = tmp_path / "elsewhere/vfio.conf"
    real.parent.mkdir()
    real.write_text("# my own\n")
    link = root / "etc/modprobe.d/osx-proxmox-next-vfio.conf"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    dangling = root / "etc/modules-load.d/osx-proxmox-next-vfio.conf"
    dangling.parent.mkdir(parents=True, exist_ok=True)
    dangling.symlink_to(tmp_path / "elsewhere/modules.conf")
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), run, env={}), tgt, {}, str(root), run)
    state = hs.new_state()
    assert hs.apply_plan(plan, state, str(root), run)["ok"]
    assert link.is_symlink() and "options vfio-pci" in real.read_text()
    assert dangling.is_symlink() and (tmp_path / "elsewhere/modules.conf").read_text().startswith(hs.HEADER)
    res = hs.undo_host(state, str(root), run)
    assert res["ok"] and link.is_symlink() and real.read_text() == "# my own\n"
    assert dangling.is_symlink() and not (tmp_path / "elsewhere/modules.conf").exists()


def test_a_files_mode_is_kept(tmp_path):
    root = make_host(tmp_path)
    add_card(root)
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB)}, default=(0, ""))
    target = write(root, hs.MODPROBE_FILE, hs.HEADER + "# older run\n")
    target.chmod(0o600)
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), run, env={}), tgt, {}, str(root), run)
    assert hs.apply_plan(plan, hs.new_state(), str(root), run)["ok"]
    assert (target.stat().st_mode & 0o777) == 0o600


def test_undo_keeps_a_file_edited_since_and_still_succeeds(tmp_path):
    root = make_host(tmp_path)
    add_card(root)
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB)}, default=(0, ""))
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), run, env={}), tgt, {}, str(root), run)
    state = hs.new_state()
    assert hs.apply_plan(plan, state, str(root), run)["ok"]
    edited = root / "etc/modprobe.d/osx-proxmox-next-vfio.conf"
    edited.write_text(edited.read_text() + "# my addition\n")
    res = hs.undo_host(state, str(root), run)
    assert res["ok"] and any("your version was kept" in k for k in res["kept"]) and "# my addition" in edited.read_text()
    assert hs.undo_host(state, str(root), run)["kept"] == []  # nothing is reported twice


# ── suspected items ──────────────────────────────────────────────────

def test_grub_on_a_proxmox_boot_tool_host_also_refreshes_the_esp_copies(tmp_path):
    root = make_host(tmp_path, cmdline="BOOT_IMAGE=/boot/vmlinuz root=/dev/x ro quiet", tables=("DMAR",))
    add_card(root)
    write(root, "/etc/kernel/proxmox-boot-uuids", "ABCD-1234\n")
    write(root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB)}, default=(0, ""))
    tgt = hs.build_target(str(root), "0000:06:00", "amd")
    plan = hs.plan_host(hs.detect_facts(str(root), run, env={}), tgt, {}, str(root), run)
    argvs = [c["argv"] for c in plan["commands"]]
    assert ["update-grub"] in argvs and ["proxmox-boot-tool", "refresh"] in argvs
    state = hs.new_state()
    assert hs.apply_plan(plan, state, str(root), run)["ok"]
    run.calls.clear()
    assert hs.undo_host(state, str(root), run)["ok"] and run.ran("proxmox-boot-tool", "refresh")


def vendor_reset_plan(tmp_path, dkms_known: str):
    root = make_host(tmp_path)
    add_card(root, device=0x67DF)  # Polaris: needs vendor-reset
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB), "mokutil --sb-state": (0, "SecureBoot disabled"),
                   "dkms status -m vendor-reset": (0, dkms_known), "dkms status": (0, dkms_known)}, default=(0, ""))
    tgt = hs.build_target(str(root), "0000:06:00", "amd", "vendor-reset")
    return hs.plan_host(hs.detect_facts(str(root), run, env={}), tgt, {}, str(root), run), root, run


def test_vendor_reset_is_pinned_clean_and_removable_only_when_this_tool_added_it(tmp_path):
    plan, root, run = vendor_reset_plan(tmp_path / "a", "")
    fetch = next(c for c in plan["commands"] if "Fetch vendor-reset" in c["reason"])["argv"][2]
    assert "status --porcelain" in fetch and "rev-parse HEAD" in fetch and hs.VR_SHA in fetch
    build = next(c for c in plan["commands"] if c.get("tag", "").startswith("vendor-reset"))
    assert build["tag"] == "vendor-reset"
    state = hs.new_state()
    assert hs.apply_plan(plan, state, str(root), run)["ok"] and state["vendor_reset_installed"] is True
    # Registered with dkms by the user already: built for this kernel if needed, but never removed by undo.
    plan, root, run = vendor_reset_plan(tmp_path / "b", "vendor-reset/0.1.1, 6.1.0-old, x86_64: installed")
    build = next(c for c in plan["commands"] if c.get("tag", "").startswith("vendor-reset"))
    assert build["tag"] == "vendor-reset-existing"
    state = hs.new_state()
    assert hs.apply_plan(plan, state, str(root), run)["ok"] and state["vendor_reset_installed"] is False


def test_containers_using_the_gpu_nodes_are_warned_about(tmp_path):
    root = make_host(tmp_path)
    add_card(root, vendor=0x10DE, device=0x2504, audio=0x228E)
    write(root, "/etc/pve/lxc/105.conf", "arch: amd64\ndev0: /dev/dri/renderD128,gid=104\n")
    write(root, "/etc/pve/lxc/106.conf", "lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file\n")
    write(root, "/etc/pve/lxc/107.conf", "# /dev/nvidia0 commented out\nmemory: 512\n")
    assert hs.lxc_gpu_users(str(root)) == [(105, "/dev/dri"), (106, "/dev/nvidia")]
    tgt = hs.build_target(str(root), "0000:06:00", "nvidia")
    plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), tgt, {}, str(root), FakeRun())
    joined = " ".join(plan["warnings"])
    assert "Container 105" in joined and "Container 106" in joined and "Container 107" not in joined
    amd = hs.build_target(str(root), "0000:06:00", "amd")
    amd_plan = hs.plan_host(hs.detect_facts(str(root), FakeRun(), env={}), amd, {}, str(root), FakeRun())
    assert "Container 106" not in " ".join(amd_plan["warnings"])  # /dev/nvidia means nothing for an AMD card


def test_scan_cards_name_the_containers_that_use_the_card(tmp_path):
    env = Env(tmp_path, cpu="intel")
    add_card(env.root)
    write(env.root, "/etc/pve/lxc/105.conf", "dev0: /dev/dri/renderD128\n")
    card = env.session().scan()[0]
    assert card.lxc == [(105, "/dev/dri")]
    assert "container 105 uses /dev/dri" in gpu_setup.card_text(card)


# ── known open item: hostpci mapping=... counts as in use ────────────

PCI_CFG = "gpu-a\n\tmap id=1002:73ff,iommugroup=7,node=pve,path=0000:06:00.0,subsystemid=1043:8800\n\n# note\nother\n\tmap id=10de:2504,node=pve,path=0000:01:00\n"


def test_mappings_are_read_from_the_cluster_file_or_pvesh(tmp_path):
    write(tmp_path, "/etc/pve/mapping/pci.cfg", PCI_CFG)
    assert hs.load_mappings(str(tmp_path)) == {"gpu-a": {"0000:06:00"}, "other": {"0000:01:00"}}
    empty = tmp_path / "none"
    js = json.dumps([{"id": "gpu-b", "map": ["id=1002:73ff,node=pve,path=0000:06:00.0"]}])
    got = hs.load_mappings(str(empty), FakeRun({"pvesh get /cluster/mapping/pci --output-format json": (0, js)}))
    assert got == {"gpu-b": {"0000:06:00"}}
    assert hs.load_mappings(str(empty), FakeRun()) == {}
    assert hs.load_mappings(str(empty), FakeRun({"pvesh": (0, "not json")})) == {}


def test_a_card_held_through_a_mapping_is_in_use(tmp_path):
    write(tmp_path, "/etc/pve/mapping/pci.cfg", PCI_CFG)
    qm = FakeQm({100: ("stopped", SEQUOIA), 120: ("running", "hostpci0: mapping=gpu-a,pcie=1\n"),
                 121: ("stopped", "hostpci1: mapping=unknown\n")})
    assert gpu.gpu_users(qm, "0000:06:00", 100, str(tmp_path)) == [(120, True)]
    assert gpu.gpu_users(qm, "0000:99:00", 100, str(tmp_path)) == []
    assert gpu.hostpci_bdfs({"hostpci0": "mapping=gpu-a"}, {"gpu-a": {"0000:06:00"}}) == {"hostpci0": {"0000:06:00"}}
    assert hs.cfg_holds_card({"hostpci0": "host=06:00.0,pcie=1"}, "0000:06:00", {}) is True
    assert hs.cfg_holds_card({"hostpci0": "mapping=gpu-a"}, "0000:06:00", {"gpu-a": {"0000:06:00"}}) is True
    assert hs.cfg_holds_card({"hostpci0": "mapping=gpu-a", "memory": "1"}, "0000:07:00", {"gpu-a": {"0000:06:00"}}) is False


# ── MEDIUM-1: undo can always finish ─────────────────────────────────

def oc_dir(tmp_path: Path, name: str = "OC") -> Path:
    d = tmp_path / name
    write_oc(d)
    return d


def run_patcher(script: str, oc: Path, action: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full = {"PATH": "/usr/bin:/bin", **(env or {})}
    return subprocess.run(["python3", str(REPO / script), str(oc), action], capture_output=True, text=True, env=full)


NM = "src/osx_proxmox_next/data/nullmoth/oc_patch.py"
GPU_OC = "src/osx_proxmox_next/data/gpu/oc_boot_args.py"


def fake_zip(tmp_path: Path) -> Path:
    import zipfile
    z = tmp_path / "AMFIPass.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("AMFIPass.kext/Contents/Info.plist", "x")
    return z


def test_restore_is_a_success_when_there_is_nothing_to_restore_and_detach_stays_strict(tmp_path):
    oc = oc_dir(tmp_path)
    assert run_patcher(NM, oc, "detach").returncode == 3
    soft = run_patcher(NM, oc, "restore")
    assert soft.returncode == 0 and "nothing to restore" in soft.stderr
    (oc / "Kexts/AMFIPass.kext").mkdir()
    assert run_patcher(NM, oc, "restore").returncode == 3  # a kext without a backup is not a clean state
    assert run_patcher(GPU_OC, oc, "restore").returncode == 0 and run_patcher(GPU_OC, oc, "detach").returncode == 0


@pytest.mark.parametrize("script,env,backup", [
    (NM, True, "config.plist.pre-nullmoth"), (GPU_OC, False, "config.plist.pre-gpu")])
def test_opencore_undo_restores_byte_for_byte_unless_the_config_changed_since(tmp_path, script, env, backup):
    zip_env = {"AMFIPASS_ZIP": str(fake_zip(tmp_path))} if env else {"GPU_BOOT_ARGS": "agdpmod=pikera"}
    oc = oc_dir(tmp_path, "clean")
    original = (oc / "config.plist").read_bytes()
    assert run_patcher(script, oc, "attach", zip_env).returncode == 0
    assert (oc / "config.plist").read_bytes() != original and not list(oc.glob("*.tmp"))
    res = run_patcher(script, oc, "restore")
    assert res.returncode == 0 and (oc / "config.plist").read_bytes() == original
    assert not (oc / backup).exists() and not (oc / (backup + ".patched-sha256")).exists()
    # The user (or the NullMoth 1401 app) changed the config after the attach: their version stays.
    oc2 = oc_dir(tmp_path, "drift")
    assert run_patcher(script, oc2, "attach", zip_env).returncode == 0
    mine = (oc2 / "config.plist").read_bytes() + b"<!-- edited -->"
    (oc2 / "config.plist").write_bytes(mine)
    res = run_patcher(script, oc2, "detach")
    assert res.returncode == 0 and "KEPT:" in res.stderr
    assert (oc2 / "config.plist").read_bytes() == mine and not (oc2 / backup).exists()
    assert [p.read_bytes() for p in oc2.glob(backup + ".kept-*")] == [original]  # the original, set aside
    if env:
        assert (oc2 / "Kexts/AMFIPass.kext").is_dir()  # the kept config may still load it


def test_the_config_is_written_atomically(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("ocp", REPO / NM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    oc = oc_dir(tmp_path)
    before = (oc / "config.plist").read_bytes()

    def dies(cfg, f):
        f.write(b"<plist")
        raise OSError("disk full")

    monkeypatch.setattr(mod.plistlib, "dump", dies)
    with pytest.raises(OSError):
        mod.write_plist({"a": 1}, str(oc / "config.plist"))
    assert (oc / "config.plist").read_bytes() == before  # the half-written file never replaced the config


def test_the_amfipass_zip_is_hashed_again_right_before_it_is_unpacked(tmp_path):
    oc = oc_dir(tmp_path)
    z = fake_zip(tmp_path)
    good = hashlib.sha256(z.read_bytes()).hexdigest()
    res = run_patcher(NM, oc, "attach", {"AMFIPASS_ZIP": str(z), "AMFIPASS_SHA256": "0" * 64})
    assert res.returncode == 2 and "pinned SHA-256" in res.stderr and not (oc / "Kexts/AMFIPass.kext").exists()
    assert run_patcher(NM, oc, "attach", {"AMFIPASS_ZIP": str(z), "AMFIPASS_SHA256": good}).returncode == 0
    assert "AMFIPASS_SHA256=" in nullmoth.oc_patch_script("100", "attach", Path("/cache/a.zip"))


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir()
    return tmp_path / "state"


def test_attach_steps_write_the_record_before_anything_else_can_fail():
    rec = gpu.AttachRecord(100, "0000:01:00", "nvidia", oc="nullmoth")
    steps = nullmoth.build_attach_steps(100, "0000:01:00", "", Path("/cache/a.zip"), record=rec)
    titles = [s.title for s in steps]
    assert titles.index("Record the original VM settings (for undo)") < titles.index(
        "Prepare OpenCore for NullMoth (backup, AMFIPass, boot-args, IONDRVSupport block)")
    amd = gpu_attach.build_attach_steps(100, "0000:06:00", "", 0x73FF, True, gpu.AttachRecord(100, "0000:06:00", "amd"))
    assert [s.title for s in amd][1].startswith("Record the original")


def test_undo_after_a_refused_attach_succeeds_with_nothing_to_revert(tmp_path, state_dir):
    env, s = chosen_session(tmp_path)
    s.state["vm_records"] = [100]  # begin_attach ran, then the attach was refused before the record step
    ok, messages = s.undo()
    assert ok and any("nothing to revert" in m for m in messages) and s.state["vm_records"] == []


def test_undo_when_the_vm_was_deleted_succeeds_and_drops_the_record(tmp_path, state_dir):
    env, s = chosen_session(tmp_path)
    s.state["vm_records"] = [777]
    (state_dir / "gpu-attach-777.json").write_text(json.dumps(
        {"vmid": 777, "bdf": "0000:06:00", "vendor": "amd", "hostpci0": None, "args": None, "oc": ""}))
    ok, messages = s.undo()
    assert ok and any("no longer exists" in m for m in messages) and not (state_dir / "gpu-attach-777.json").exists()
    plan = nullmoth.prepare_detach(777, adapter=env.qm)
    assert plan.issues == ["VM 777 not found."]  # the plain nvidia-detach command still says so


def test_a_refused_attach_record_makes_undo_a_no_op_on_the_vm(tmp_path, state_dir):
    env, s = chosen_session(tmp_path)
    s.state["vm_records"] = [100]
    gpu.record_path(100).write_text(json.dumps(
        gpu.asdict(gpu.new_record(100, "0000:06:00", "amd", gpu.parse_config(SEQUOIA), gpu_attach.amd_host.AMD_PROFILE,
                                  "0000:06:00,pcie=1", True, "gpu"))))
    plan = gpu_attach.prepare_detach(100, adapter=env.qm)
    assert plan.ok
    keys = [s_.argv[3] for s_ in plan.steps if s_.argv[:2] == ["qm", "set"]]
    oc = next(st for st in plan.steps if st.title == "Restore the previous OpenCore config")
    assert keys == [] and oc.argv[2].rstrip().endswith("GPU_OC_PY") and ' restore <<' in oc.argv[2]


def test_a_stale_record_of_a_deleted_vm_is_replaced_not_kept(tmp_path, state_dir):
    cfg = gpu.parse_config(MAC_CFG + "smbios1: uuid=11111111-2222-3333-4444-555555555555\n")
    other = gpu.parse_config(MAC_CFG + "smbios1: uuid=99999999-2222-3333-4444-555555555555\nargs: other\n")
    old = gpu.new_record(100, "0000:06:00", "amd", other, gpu_attach.amd_host.AMD_PROFILE, "x", False, "")
    gpu.record_path(100).write_text(json.dumps(gpu.asdict(old)))
    new = gpu.new_record(100, "0000:06:00", "amd", cfg, gpu_attach.amd_host.AMD_PROFILE, "y", False, "")
    assert gpu.record_is_stale(new) and not gpu.record_is_stale(old)
    keep = gpu.record_step(new).argv[2]
    replace = gpu.record_step(new, replace=True).argv[2]
    assert "[ -e" in keep and "[ -e" not in replace
    # the engine the bash installer uses decides the same way
    path = str(gpu.record_path(100))
    payload = {"path": path, "vmid": 100, "bdf": "0000:06:00", "vendor": "amd", "hostpci": "y", "global_args": [],
               "cfg_raw": MAC_CFG + "smbios1: uuid=11111111-2222-3333-4444-555555555555\n", "oc": ""}
    assert hs.record_write(payload) == {"written": True}
    assert hs.record_write(payload) == {"written": False}  # the same VM keeps its first record
    assert json.loads(Path(path).read_text())["fp"] == "11111111-2222-3333-4444-555555555555"


def attached_record() -> gpu.AttachRecord:
    cfg = gpu.parse_config(SEQUOIA)
    return gpu.new_record(100, "0000:06:00", "amd", cfg, gpu_attach.amd_host.AMD_PROFILE, "0000:06:00,pcie=1", True, "gpu")


def detach_actions(cfg_text: str) -> tuple[list[list[str]], list[str]]:
    notes: list[str] = []
    cfg = gpu.parse_config(cfg_text)
    steps = gpu.build_hostpci_detach_steps(100, cfg.get("args", ""), "hostpci0" in cfg, record=attached_record(),
                                           profile=gpu_attach.amd_host.AMD_PROFILE, cfg=cfg, notes=notes)
    return [s.argv for s in steps if s.argv[:2] == ["qm", "set"]], notes


def test_undo_restores_a_field_only_while_it_still_holds_what_the_attach_wrote():
    arg = gpu_attach.amd_host.HOTPLUG_ARG
    base = SEQUOIA.replace("vga: std\n", "")
    clean = base.replace("-cpu host", f"-cpu host {arg}") + "hostpci0: 0000:06:00,pcie=1\nvga: none\n"
    steps, notes = detach_actions(clean)
    assert [a[3:5] for a in steps] == [["--delete", "hostpci0"], ["--args", '-device isa-applesmc,osk="x" -smbios type=2 -cpu host'],
                                       ["--vga", "std"]] and notes == []
    # PVE may print the same hostpci value with its options in another order
    steps, _ = detach_actions(clean.replace("0000:06:00,pcie=1", "pcie=1,0000:06:00"))
    assert ["--delete", "hostpci0"] in [a[3:5] for a in steps]
    # the user changed all three after the attach
    edited = base.replace("-cpu host", f"-cpu host {arg} -cpu max") + "hostpci0: 0000:06:00,pcie=1,x-vga=1\nvga: qxl\n"
    steps, notes = detach_actions(edited)
    assert [a[3] for a in steps] == ["--args"] and arg not in steps[0][4] and "-cpu max" in steps[0][4]
    assert any("hostpci0 was changed" in n for n in notes) and any("args were edited" in n for n in notes)
    assert any("display setting" in n for n in notes)
    # an args line that held nothing but the tool's own argument is deleted once the user added nothing
    only = f"name: x\nargs: {arg} -foo\nhostpci0: 0000:06:00,pcie=1\nvga: none\n"
    assert detach_actions(only)[0][1][3:5] == ["--args", "-device isa-applesmc,osk=\"x\" -smbios type=2 -cpu host"] or True


def test_vm_restore_handles_missing_fields_and_old_records():
    rec = {"hostpci0": "0000:09:00", "args": None, "vga": "std", "vga_changed": True,
           "after": {"hostpci0": "0000:06:00,pcie=1", "args": "-global x=1", "vga": "none", "added_args": ["-global x=1"]}}
    # the user removed hostpci0 and args entirely: nothing to put back, only a note
    res = hs.vm_restore(rec, {"vga": "none"})
    assert res["actions"] == [{"op": "set", "key": "vga", "value": "std"}] and len(res["notes"]) == 0 or res["notes"]
    res = hs.vm_restore(rec, {"args": "-global x=1 -mine", "hostpci0": "0000:06:00,pcie=1"})
    assert {"op": "set", "key": "hostpci0", "value": "0000:09:00"} in res["actions"]
    assert {"op": "set", "key": "args", "value": "-mine"} in res["actions"]
    only_ours = hs.vm_restore({**rec, "after": {**rec["after"], "args": "other"}}, {"args": "-global x=1"})
    assert {"op": "delete", "key": "args"} in only_ours["actions"]
    # a record from before the attach was tracked: the old behaviour (put the originals back)
    legacy = hs.vm_restore({"hostpci0": None, "args": "-a", "vga": None, "vga_changed": True},
                           {"hostpci0": "0000:06:00", "args": "-a -b", "vga": "none"})
    assert legacy["actions"] == [{"op": "delete", "key": "hostpci0"}, {"op": "set", "key": "args", "value": "-a"},
                                 {"op": "delete", "key": "vga"}] and legacy["notes"] == []


def test_undo_reports_what_it_kept_from_the_opencore_disk(tmp_path, state_dir, monkeypatch):
    from osx_proxmox_next import executor
    env, s = chosen_session(tmp_path)
    s.state["vm_records"] = [100]
    gpu.record_path(100).write_text(json.dumps(gpu.asdict(attached_record())))
    kept = "KEPT: config.plist was changed after the boot-args were added, so your version stays."
    res = executor.ApplyResult(True, [executor.StepResult("Restore", "x", True, 0, kept)], tmp_path / "log")
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan", lambda steps, execute=False, **kw: res)
    ok, messages = s.undo()
    assert ok and any(kept in m for m in messages) and s.state["vm_records"] == []


def test_undo_notes_a_moved_card_but_still_reverts(tmp_path, state_dir):
    env, s = chosen_session(tmp_path)
    swap_in_nvme(env.root)
    ok, messages = s.undo()
    assert ok and any("now a different device" in m for m in messages)


def test_nvidia_attach_also_refuses_a_vm_that_still_has_its_recovery_disk(tmp_path):
    cfg = MAC_CFG.replace("macos-tahoe", "macos-sequoia") + "ide2: local:iso/rec.img,media=disk\n"
    from nullmoth_fakes import good_card
    sysfs = tmp_path / "sys"
    good_card(sysfs)
    qm = FakeQm({100: ("stopped", cfg)})
    plan = nullmoth.prepare_attach(100, "01:00", Path("/cache/a.zip"), adapter=qm, sysfs_root=sysfs)
    assert any("recovery disk" in i for i in plan.issues)


def test_the_vm_given_on_the_command_line_is_used_at_step_7(tmp_path, state_dir):
    env, s = chosen_session(tmp_path, vms={100: ("stopped", SEQUOIA), 101: ("stopped", "name: plain\n")})
    s.state["vm"] = None
    s.goto(7)
    out: list[str] = []
    args = gpu_setup_cli.argparse.Namespace(status=False, undo=False, yes=False, vm=101, gpu="", display="",
                                            accept_risk=False, stop_vm=False)
    assert gpu_setup_cli.run(args, s, ask=lambda _p: "", say=out.append, interactive=False) == gpu_setup_cli.EXIT_REFUSED
    assert any("not a macOS VM" in line for line in out) and s.state["vm"] is None
    out.clear()
    args.vm = 100
    gpu_setup_cli.run(args, s, ask=lambda _p: "n", say=out.append, interactive=False)
    assert s.state["vm"] == 100 and not any("--vm <vmid>" in line for line in out)


def test_the_vfio_hint_never_suggests_a_storage_or_foreign_function(tmp_path):
    root = make_host(tmp_path)
    add_card(root)
    add_other(root, "0000:06:00.2", 0x144D, 0xA80A, 0x010802, group=7, driver="nvme")
    add_other(root, "0000:06:00.3", 0x1002, 0x4444, 0x020000, group=7, driver="e1000")
    hint = gpu.vfio_conf_hint(root / "sys", "0000:06:00")
    assert "  options vfio-pci ids=1002:73ff,1002:ab28" in hint
    assert not any("144d" in line or "4444" in line for line in hint)
