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
    # pve-mirna: nouveau is shipped with the kernel, the proprietary nvidia module is not installed
    "/lib/modules/7.0.14-25-pve/modules.dep": ("kernel/drivers/gpu/drm/nouveau/nouveau.ko: kernel/drivers/gpu/drm/drm.ko\n"
                                               "kernel/drivers/video/fbdev/nvidia/nvidiafb.ko:\n"),
}
# pve-mirna's vfio.conf ids (10de:2489,10de:228b) are stale: the cards are 10de:2503 and 10de:2504, audio 10de:228e.
# Nothing binds them at boot; vfio_pci is loaded from /etc/modules and no host driver can claim them.


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
    assert any("already bound to vfio-pci and no host driver claims it at boot" in s for s in p["skipped"])
    assert any("Proxmox hands the card to vfio-pci when the VM starts; no host driver claims it at boot." in n
               for n in p["notes"])
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


def _with(extra, drop=()):
    files = {k: v for k, v in FILES.items() if k not in drop}
    files.update(extra)
    return files


def _required(p):
    paths = {c["path"] for c in p["changes"]}
    return not p["optional"] and hs.MODPROBE_FILE in paths and not any("already bound" in x for x in p["skipped"])


def test_softdeps_alone_still_write_when_a_host_driver_could_claim_the_card(tmp_path):
    # nouveau is installed and not blacklisted: bound only by a VM start, nothing orders vfio-pci at boot
    nosoft = {"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n"}
    p = plan(mirna(tmp_path / "z", _with(nosoft, drop=("/etc/modprobe.d/vfio-nvidia.conf",))))
    assert _required(p) and not any("may be lost" in w for w in p["warnings"])
    # softdeps present but nouveau still open: ordered first, nothing more to write
    open_nouveau = _with({"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n",
                          "/etc/modprobe.d/vfio-nvidia.conf": FILES["/etc/modprobe.d/vfio-nvidia.conf"].replace(
                              "blacklist nouveau\n", "")})
    p = plan(mirna(tmp_path / "a", open_nouveau))
    assert not p["optional"] and not any("Proxmox hands" in n for n in p["notes"])
    # the proprietary driver is installed and not blacklisted
    nv = _with({"/lib/modules/7.0.14-25-pve/modules.dep": "updates/dkms/nvidia.ko.zst: \n"})
    nv["/etc/modprobe.d/pve-blacklist.conf"] = "blacklist nouveau\n"
    nv["/etc/modprobe.d/vfio-nvidia.conf"] = ""
    assert _required(plan(mirna(tmp_path / "b", nv)))
    # no modules.dep to read: unknown counts as claimable
    unknown = {k: v for k, v in _with({"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n"},
                                     drop=("/etc/modprobe.d/vfio-nvidia.conf",)).items() if "modules.dep" not in k}
    assert _required(plan(mirna(tmp_path / "c", unknown)))


def test_ids_alone_do_not_count_when_a_host_driver_could_claim_the_card(tmp_path):
    open_nouveau = {"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n"}
    ids = _with({**open_nouveau, "/etc/modprobe.d/x.conf": "options vfio-pci ids=10de:2504,10de:228e\n"},
                drop=("/etc/modprobe.d/vfio-nvidia.conf",))
    p = plan(_lone(tmp_path / "a", ids))
    assert _required(p) and any("softdep nouveau pre: vfio-pci" in c["new"] for c in p["changes"])
    cmd = _with({**open_nouveau, "/etc/default/grub": GRUB.replace("quiet", "quiet vfio-pci.ids=10de:2504,10de:228e", 1)},
                drop=("/etc/modprobe.d/vfio-nvidia.conf",))
    assert _required(plan(_lone(tmp_path / "b", cmd)))
    # ordered first (softdeps) the same ids are a real binding
    ordered = _with({"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n",
                     "/etc/modprobe.d/vfio-nvidia.conf": FILES["/etc/modprobe.d/vfio-nvidia.conf"].replace(
                         "blacklist nouveau\n", ""),
                     "/etc/modprobe.d/x.conf": "options vfio-pci ids=10de:2504,10de:228e\n"})
    p = plan(_lone(tmp_path / "c", ordered))
    assert not p["optional"] and p["changes"] == [] and any("already bound" in x for x in p["skipped"])


def test_a_partial_driver_override_script_does_not_count(tmp_path):
    open_nouveau = {"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n"}
    audio_only = _with({**open_nouveau, "/etc/initramfs-tools/scripts/init-top/mine":
                        "#!/bin/sh\necho vfio-pci > /sys/bus/pci/devices/0000:06:00.1/driver_override\n"},
                       drop=("/etc/modprobe.d/vfio-nvidia.conf",))
    assert _required(plan(mirna(tmp_path, audio_only)))


def test_a_bind_script_from_an_older_card_in_the_slot_does_not_count(tmp_path):
    old = hs._bind_script([("0000:06:00.0", "0x10de", "0x1e87", "0x03", "-", "-"),
                           ("0000:06:00.1", "0x10de", "0x10f8", "0x0403", "-", "-")])
    files = _with({"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n", hs.BIND_SCRIPT: old},
                  drop=("/etc/modprobe.d/vfio-nvidia.conf",))
    p = plan(mirna(tmp_path / "a", files))
    assert _required(p) and hs.BIND_SCRIPT in {c["path"] for c in p["changes"]}
    cur = hs._bind_script([("0000:06:00.0", "0x10de", "0x2504", "0x03", "-", "-"),
                           ("0000:06:00.1", "0x10de", "0x228e", "0x0403", "-", "-")])
    files = _with({"/etc/modprobe.d/pve-blacklist.conf": "blacklist nvidiafb\n", hs.BIND_SCRIPT: cur},
                  drop=("/etc/modprobe.d/vfio-nvidia.conf",))
    p = plan(mirna(tmp_path / "b", files))
    assert not p["optional"] and any("a-osx-proxmox-next-gpu-bind" in x for x in p["skipped"])


def test_softdeps_with_every_claimant_blocked_is_already_ok(tmp_path):
    # nvidia not installed (no module), nouveau blacklisted
    p = plan(mirna(tmp_path))
    assert not p["optional"] and p["changes"] == []
    # nouveau absent from the kernel and nothing blacklisted is just as safe
    none = _with({"/lib/modules/7.0.14-25-pve/modules.dep": "kernel/fs/ext4.ko:\n",
                  "/etc/modprobe.d/pve-blacklist.conf": "# empty\n",
                  "/etc/modprobe.d/vfio-nvidia.conf": FILES["/etc/modprobe.d/vfio-nvidia.conf"].replace(
                      "blacklist nouveau\n", "")})
    assert not plan(mirna(tmp_path / "x", none))["optional"]


def test_stale_ids_in_modprobe_do_not_count_as_a_binding(tmp_path):
    # pve-mirna's vfio.conf names 10de:2489 and 10de:228b, not the installed cards
    nosoft = {k: v for k, v in FILES.items() if "vfio-nvidia" not in k}
    assert plan(mirna(tmp_path, nosoft))["optional"] is True


def test_amd_softdeps_alone_always_plan_the_binding(tmp_path):
    root = make_host(tmp_path, cmdline=CMDLINE)
    for path, text in FILES.items():
        write(root, path, text)
    write(root, "/etc/modprobe.d/vfio-amd.conf", "softdep amdgpu pre: vfio-pci\nsoftdep radeon pre: vfio-pci\n"
          "softdep snd_hda_intel pre: vfio-pci\n")
    add_card(root, bdf="0000:06:00", group=2, boot_vga=True)  # AMD defaults, bound to vfio-pci now
    run = FakeRun({"efibootmgr -v": (0, "BootCurrent: 0000\n")})
    p = hs.plan_host(hs.detect_facts(str(root), run, env={}), hs.build_target(str(root), "0000:06:00", "amd", "none"),
                     {}, str(root), run)
    assert not p["optional"] and hs.MODPROBE_FILE in {c["path"] for c in p["changes"]}
    assert not any("already bound" in s for s in p["skipped"])


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
                           "#!/bin/sh\necho vfio-pci > /sys/bus/pci/devices/0000:06:00.0/driver_override\n"
                           "echo vfio-pci > /sys/bus/pci/devices/0000:06:00.1/driver_override\n"})
    assert not plan(mirna(tmp_path / "c", script))["optional"]
    udev = dict(base, **{"/etc/udev/rules.d/90-vfio.rules":
                         'ACTION=="add", KERNEL=="0000:06:00.0|0000:06:00.1", ATTR{driver_override}="vfio-pci" # driver_override\n'})
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

def _cli_host(tmp_path, no_claimer=True):
    from test_gpu_setup import Env, ready
    env = Env(tmp_path, cpu="intel")
    add_card(env.root, vendor=0x10DE, device=0x2504, audio=0x228E)  # bound to vfio-pci now, nothing persistent
    ready(env)
    write(env.root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    if no_claimer:
        write(env.root, "/etc/modprobe.d/pve-blacklist.conf", "blacklist nouveau\nblacklist nvidia\n")
    s = env.session()
    assert s.choose("06:00", 100, "gpu", ack=True) == []
    s.goto(4)
    s.state["done"] = [1, 2, 3]
    s.save()
    return env, s


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

def _bash_host(bash, vm, tmp_path, nvidia=False):
    from test_gpu_hardening_bash import chosen_state, walkthrough_env
    if nvidia:
        import json
        root, state_dir, env = walkthrough_env(tmp_path, vm)
        import shutil
        shutil.rmtree(root / "sys/bus/pci/devices/0000:06:00.0")
        shutil.rmtree(root / "sys/bus/pci/devices/0000:06:00.1")
        for link in (root / "sys/kernel/iommu_groups").glob("*/devices/0000:06:00.*"):
            link.unlink()
        add_card(root, vendor=0x10DE, device=0x2504, audio=0x228E)
        stub = bash.stub_bin / "lspci"
        stub.write_text('#!/bin/sh\necho "06:00.0 VGA compatible controller [0300]: Test Card [10de:2504]"\n')
        stub.chmod(0o755)
        bash("gpu_setup --yes --vm 100 --gpu 06:00 --accept-nullmoth-risk", env=env, expect=None)
        assert json.loads((state_dir / "gpu-setup.json").read_text())["gpu"]["identity"]["vendor"] == 0x10DE
    else:
        root, _state_dir, env, _state = chosen_state(bash, vm, tmp_path)
    write(root, "/proc/cmdline", "BOOT_IMAGE=/x ro intel_iommu=on iommu=pt\n")
    write(root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    write(root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"\n')
    return root, env


def test_bash_host_step_amd_softdeps_alone_still_plan_the_binding(bash, vm, tmp_path):
    root, env = _bash_host(bash, vm, tmp_path)
    write(root, "/etc/modprobe.d/vfio-gpu.conf", "softdep amdgpu pre: vfio-pci\nsoftdep radeon pre: vfio-pci\n"
                                                   "softdep snd_hda_intel pre: vfio-pci\n")
    res = bash("gpu_step_host", env={**env, "GPU_YES": "yes"}, expect=None)
    assert "already bound to vfio-pci" not in res.stdout and "may be lost after a reboot" not in res.stdout
    assert "This is exactly what will change" in res.stdout and hs.MODPROBE_FILE in res.stdout


def test_bash_host_step_nvidia_with_no_claiming_driver_is_already_ok(bash, vm, tmp_path):
    root, env = _bash_host(bash, vm, tmp_path, nvidia=True)
    write(root, "/etc/modprobe.d/vfio-nvidia.conf", "softdep nouveau pre: vfio-pci\nsoftdep nvidia pre: vfio-pci\n"
                                                      "softdep snd_hda_intel pre: vfio-pci\nblacklist nouveau\n")
    write(root, "/etc/modprobe.d/pve-blacklist.conf", "blacklist nvidia\n")
    write(root, "/lib/modules/7.0.14-25-pve/modules.dep", "kernel/drivers/gpu/drm/nouveau/nouveau.ko:\n")
    res = bash("gpu_step_host", env=env, expect=None)
    assert "no host driver claims it at boot" in res.stdout
    assert "This is exactly what will change" not in res.stdout and not (root / hs.MODPROBE_FILE.lstrip("/")).exists()


def test_bash_host_step_warns_and_skips_the_optional_write_by_default(bash, vm, tmp_path):
    root, env = _bash_host(bash, vm, tmp_path, nvidia=True)  # bound now, nothing persistent
    write(root, "/etc/modprobe.d/pve-blacklist.conf", "blacklist nouveau\nblacklist nvidia\n")  # no claimer
    res = bash("gpu_step_host", env={**env, "GPU_YES": "yes"}, expect=None)
    assert "may be lost after a reboot" in res.stdout and "This is exactly what will change" not in res.stdout
    assert not (root / hs.MODPROBE_FILE.lstrip("/")).exists()


def test_bash_host_step_writes_when_a_driver_could_claim_the_card(bash, vm, tmp_path):
    root, env = _bash_host(bash, vm, tmp_path, nvidia=True)  # nothing blocks nouveau
    res = bash("gpu_step_host", env={**env, "GPU_YES": "yes"}, expect=None)
    assert "may be lost after a reboot" not in res.stdout and "already bound" not in res.stdout
    assert "This is exactly what will change" in res.stdout and hs.MODPROBE_FILE in res.stdout


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


def test_cli_applies_the_write_without_asking_when_a_driver_could_claim_the_card(tmp_path):
    env, s = _cli_host(tmp_path, no_claimer=False)
    code, out = _step(s, [], yes=True)
    assert "Write the boot-time binding" not in out and "may be lost" not in out
    assert (env.root / hs.MODPROBE_FILE.lstrip("/")).exists() and 4 not in s.state.get("skipped", [])
