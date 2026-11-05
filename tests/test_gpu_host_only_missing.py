"""The host step plans only what is missing: replay of pve-mirna's real files (live test 2)."""
from __future__ import annotations

from pathlib import Path

from gpu_fakes import FakeRun, add_card, make_host, write
from osx_proxmox_next import host_setup as hs
from test_gpu_parity import bash, block_file, vm  # noqa: F401  (fixtures)

CMDLINE = ("BOOT_IMAGE=/boot/vmlinuz-7.0.14-25-pve root=/dev/mapper/pve-root ro quiet intel_iommu=on iommu=pt "
           "video=vesafb:off video=efifb:off initcall_blacklist=sysfb_init processor.max_cstate=1 intel_idle.max_cstate=0")
GRUB = ('GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt video=vesafb:off video=efifb:off '
        'initcall_blacklist=sysfb_init processor.max_cstate=1 intel_idle.max_cstate=0"\nGRUB_CMDLINE_LINUX=""\n')
FILES = {
    "/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\nblacklist nouveau\nblacklist nvidia\nblacklist snd_hda_intel\n",
    "/etc/modprobe.d/vfio.conf": "options vfio-pci ids=10de:2489,10de:228b disable_vga=1\n",
    "/etc/modprobe.d/vfio-nvidia.conf": ("softdep nouveau pre: vfio-pci\nsoftdep nvidia pre: vfio-pci\n"
                                         "softdep snd_hda_intel pre: vfio-pci\nblacklist nouveau\n"),
    "/etc/modprobe.d/kvm.conf": "options kvm ignore_msrs=Y report_ignored_msrs=0\n",
    "/etc/modules": "vfio\nvfio_iommu_type1\nvfio_pci\nvfio_virqfd\n",
    "/etc/default/grub": GRUB,
}


def mirna(tmp_path: Path, files: dict | None = None, driver: str | None = "vfio-pci") -> str:
    root = make_host(tmp_path, cmdline=CMDLINE)
    for path, text in (FILES if files is None else files).items():
        write(root, path, text)
    add_card(root, bdf="0000:01:00", vendor=0x10DE, device=0x2503, audio=0x228E, driver=driver, group=1)
    add_card(root, bdf="0000:06:00", vendor=0x10DE, device=0x2504, audio=0x228E, driver=driver, group=2, boot_vga=True)
    return str(root)


def plan(root: str, **opts) -> dict:
    run = FakeRun({"efibootmgr -v": (0, "BootCurrent: 0000\nBoot0000* proxmox\tFile(\\EFI\\proxmox\\shimx64.efi)\n")})
    return hs.plan_host(hs.detect_facts(root, run, env={}), hs.build_target(root, "0000:06:00", "nvidia", "none"),
                        opts, root, run)


def test_mirna_host_already_passes_the_card_through_so_nothing_is_planned(tmp_path):
    p = plan(mirna(tmp_path))
    assert p["changes"] == [] and p["commands"] == [] and not p["optional"] and p["refusals"] == []
    assert any("already bound to vfio-pci and set to bind at boot" in s for s in p["skipped"])
    assert any("Kernel arguments are already set" in s for s in p["skipped"])
    assert any("VFIO modules are already listed" in s for s in p["skipped"])
    assert not hs.new_plan()["optional"] and p["reboot_required"] is False


def _lone(tmp_path, files):
    root = mirna(tmp_path, files)
    import shutil
    for fn in Path(root, "sys/bus/pci/devices").glob("0000:01:00.*"):
        shutil.rmtree(fn)
    for link in Path(root, "sys/kernel/iommu_groups").glob("*/devices/0000:01:00.*"):
        link.unlink()
    return root


def test_bound_now_but_nothing_persistent_is_a_warning_and_an_optional_write(tmp_path):
    files = {k: v for k, v in FILES.items() if "vfio-nvidia" not in k}
    root = mirna(tmp_path, files)
    p = plan(root)
    assert p["changes"] == [] and p["commands"] == [] and p["optional"] is True
    assert any("bound to vfio-pci now" in w and "may be lost after a reboot" in w for w in p["warnings"])
    wanted = plan(root, write_optional=True)
    paths = {c["path"] for c in wanted["changes"]}
    assert hs.MODPROBE_FILE in paths and hs.BIND_SCRIPT in paths


def test_not_bound_now_still_plans_the_binding(tmp_path):
    p = plan(mirna(tmp_path, driver=None))
    assert {c["path"] for c in p["changes"]} >= {hs.MODPROBE_FILE, hs.BIND_SCRIPT} and not p["optional"]


def test_each_persistent_source_counts(tmp_path):
    base = {k: v for k, v in FILES.items() if "vfio-nvidia" not in k}
    # a lone card (no identical twin): ids in modprobe.d or on the next-boot command line are enough
    ids = dict(base, **{"/etc/modprobe.d/x.conf": "options vfio-pci ids=10de:2504,10de:228e\n"})
    lone = lambda d, f: _lone(d, f)  # noqa: E731
    assert not plan(lone(tmp_path / "a", ids))["optional"]
    cmd = dict(base, **{"/etc/default/grub": GRUB.replace("quiet", "quiet vfio-pci.ids=10de:2504,10de:228e", 1)})
    assert not plan(lone(tmp_path / "b", cmd))["optional"]
    script = dict(base, **{"/etc/initramfs-tools/scripts/init-top/mine":
                           "#!/bin/sh\necho vfio-pci > /sys/bus/pci/devices/0000:06:00.0/driver_override\n"})
    assert not plan(mirna(tmp_path / "c", script))["optional"]
    udev = dict(base, **{"/etc/udev/rules.d/90-vfio.rules":
                         'ACTION=="add", KERNEL=="0000:06:00.*", ATTR{driver_override}="vfio-pci" # driver_override\n'})
    assert not plan(mirna(tmp_path / "d", udev))["optional"]
    partial = dict(base, **{"/etc/modprobe.d/x.conf": "options vfio-pci ids=10de:2504\n"})
    assert plan(mirna(tmp_path / "e", partial))["optional"]
    half_softdeps = dict(base, **{"/etc/modprobe.d/x.conf": "softdep nouveau pre: vfio-pci\n"})
    assert plan(mirna(tmp_path / "f", half_softdeps))["optional"]


def test_missing_kernel_arg_is_still_planned_on_an_otherwise_ready_host(tmp_path):
    files = dict(FILES, **{"/etc/default/grub": 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'})
    root = mirna(tmp_path, files)
    write(Path(root), "/proc/cmdline", "BOOT_IMAGE=/boot/vmlinuz ro quiet\n")
    p = plan(root)
    assert hs.GRUB_DROPIN in {c["path"] for c in p["changes"]}
    assert not any(c["path"] in (hs.MODPROBE_FILE, hs.BIND_SCRIPT) for c in p["changes"])


# ── the CLI step: optional write defaults to skip ──

def _cli_host(tmp_path):
    from test_gpu_setup import Env, ready
    from test_gpu_setup_cli import at_step
    env = Env(tmp_path, cpu="intel")
    add_card(env.root)  # bound to vfio-pci now, nothing persistent on disk
    ready(env)
    write(env.root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    return env, at_step(env, 4)


def _step(s, answers, yes=False):
    import argparse

    from osx_proxmox_next import gpu_setup_cli as cli
    out, queue = [], list(answers)
    io = cli._Io(lambda p: (out.append(p), queue.pop(0))[1], out.append, True)
    code = cli._step_host(s, io, argparse.Namespace(yes=yes))
    return code, "\n".join(out)


def test_cli_offers_the_optional_write_and_skips_it_by_default(tmp_path):
    env, s = _cli_host(tmp_path)
    code, out = _step(s, ["", ])  # Enter = the default, no
    assert "may be lost after a reboot" in out and "Write the boot-time binding anyway? (optional) [y/N]" in out
    assert code is None and 4 in s.state["skipped"] and not (env.root / hs.MODPROBE_FILE.lstrip("/")).exists()
    assert "This is exactly what will change" not in out


def test_cli_writes_the_optional_binding_only_when_asked(tmp_path):
    env, s = _cli_host(tmp_path)
    code, out = _step(s, ["y", "n"])
    assert "This is exactly what will change" in out and hs.MODPROBE_FILE in out
    assert "Nothing was changed." in out and not (env.root / hs.MODPROBE_FILE.lstrip("/")).exists()


def test_cli_yes_never_answers_the_optional_question(tmp_path):
    _env, s = _cli_host(tmp_path)
    code, out = _step(s, [], yes=True)
    assert code is None and "Write the boot-time binding" not in out and 4 in s.state["skipped"]


# ── undo of an empty record ──

def test_undo_of_an_empty_record_does_not_say_nothing_to_undo(tmp_path):
    from test_gpu_setup import Env
    env = Env(tmp_path, cpu="intel")
    add_card(env.root)
    s = env.session()
    s.save()  # an empty gpu-setup.json
    assert hs.load_state(str(env.root)) is not None
    ok, msgs = s.undo()
    assert ok and msgs == ["Removed an empty setup record; nothing else to undo."]
    assert hs.load_state(str(env.root)) is None
    ok, msgs = env.session().undo()
    assert msgs == ["Nothing to undo: no GPU setup record found."]


# ── NVIDIA display guidance ──

def test_nvidia_guidance_says_a_monitor_or_dummy_plug_is_required():
    from osx_proxmox_next import gpu_setup, nullmoth
    need = ("monitor or an HDMI/DisplayPort dummy plug", "Screen Sharing", "SSH always works")
    first = nullmoth.NEXT_STEPS[0]
    assert all(n in first for n in need) and "vga std" in first
    prep = "\n".join(nullmoth.GUEST_PREP)
    assert "dummy plug" in prep and "stay black" in prep
    assert "dummy plug" in nullmoth.GUEST_PREP_CONFIRM and "monitor" in nullmoth.GUEST_PREP_CONFIRM
    assert "dummy plug" in gpu_setup.DISPLAY_OPTIONS[1][2]


def test_nvidia_guidance_is_in_docs_readme_and_bash():
    root = Path(__file__).resolve().parent.parent
    for rel in ("README.md", "docs/docs/guides/gpu-passthrough.md", "docs/docs/guides/nvidia-nullmoth.md",
                "docs/docs/guides/cli-reference.md", "scripts/bash/osx-proxmox-next.sh"):
        text = (root / rel).read_text()
        assert "dummy plug" in text, rel
    sh = (root / "scripts/bash/osx-proxmox-next.sh").read_text()
    assert "a monitor or dummy plug is connected to the card." in sh and "Screen Sharing then mirrors that display" in sh
    assert "Removed an empty setup record; nothing else to undo." in sh
    assert "Write the boot-time binding anyway? (optional)" in sh


# ── bash parity ──

def _bash_host(bash, vm, tmp_path):
    from test_gpu_hardening_bash import chosen_state
    root, state_dir, env, _state = chosen_state(bash, vm, tmp_path)
    write(root, "/proc/cmdline", "BOOT_IMAGE=/x ro intel_iommu=on iommu=pt\n")
    write(root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    write(root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"\n')
    return root, env


def test_bash_host_step_proposes_nothing_when_the_binding_is_already_persistent(bash, vm, tmp_path):
    root, env = _bash_host(bash, vm, tmp_path)
    write(root, "/etc/modprobe.d/vfio-gpu.conf", "softdep amdgpu pre: vfio-pci\nsoftdep radeon pre: vfio-pci\n"
                                                   "softdep snd_hda_intel pre: vfio-pci\n")
    res = bash("gpu_step_host", env=env, expect=None)
    assert "already bound to vfio-pci and set to bind at boot" in res.stdout
    assert "This is exactly what will change" not in res.stdout and "Apply these changes" not in res.stdout
    assert not (root / hs.MODPROBE_FILE.lstrip("/")).exists()


def test_bash_host_step_warns_and_skips_the_optional_write_by_default(bash, vm, tmp_path):
    root, env = _bash_host(bash, vm, tmp_path)
    res = bash("gpu_step_host", env={**env, "GPU_YES": "yes"}, expect=None)
    assert "may be lost after a reboot" in res.stdout and "This is exactly what will change" not in res.stdout
    assert not (root / hs.MODPROBE_FILE.lstrip("/")).exists()


def test_bash_undo_of_an_empty_record(bash, vm, tmp_path):
    vm(100, "stopped", "name: x\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / "gpu-setup.json").write_text("{}")
    res = bash("gpu_setup --undo", env={**vm.env, "GPU_STATE_DIR": str(state)})
    assert "Removed an empty setup record; nothing else to undo." in res.stdout
    assert "Nothing to undo" not in res.stdout and not (state / "gpu-setup.json").exists()


# ── audit follow-ups ──

def test_the_live_cmdline_alone_is_not_a_boot_binding(tmp_path):
    base = {k: v for k, v in FILES.items() if "vfio-nvidia" not in k}
    root = mirna(tmp_path, base)
    write(Path(root), "/proc/cmdline", CMDLINE + " vfio-pci.ids=10de:2504,10de:228e\n")
    assert plan(root)["optional"]  # grub no longer has it, so the next boot would lose it


def test_ids_covering_a_card_with_an_identical_twin_still_refuse(tmp_path):
    files = dict(FILES, **{"/etc/modprobe.d/x.conf": "options vfio-pci ids=10de:2504,10de:228e\n"})
    p = plan(mirna(tmp_path, files))
    assert any("binds the id" in r for r in p["refusals"]) and not p["optional"]
