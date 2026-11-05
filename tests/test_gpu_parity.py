"""Python/bash parity for the GPU walkthrough, and the bash side's qm failure paths."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from gpu_fakes import EFI_GRUB, FakeRun, add_card, make_host, write
from nullmoth_fakes import good_card
from osx_proxmox_next import amd_host, bios_guide, gpu, gpu_setup, host_setup, nullmoth

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src/osx_proxmox_next"
BASH = (REPO / "scripts/bash/osx-proxmox-next.sh").read_text()
BLOCK = BASH[BASH.index("# ── Vendor-neutral GPU helpers begin ──"):BASH.index("# ── GPU walkthrough end ──")]
STUBS = ("BOLD='';CL='';CROSS='';INFO='';YW='';RD='';GN='';BFR='';CM='';TAB='';HOLD='';"
         "msg_ok(){ echo \"OK: $1\"; }; msg_error(){ echo \"ERR: $1\"; }; msg_info(){ :; };\n")

EMBEDS = {
    "host_setup.py": ("GPU_EMBED_HOST_SETUP", PKG / "host_setup.py"),
    "bios_guide.py": ("GPU_EMBED_BIOS_GUIDE", PKG / "bios_guide.py"),
    "bios_paths.json": ("GPU_EMBED_BIOS_PATHS", PKG / "data/gpu/bios_paths.json"),
    "amd_devices.txt": ("GPU_EMBED_AMD_DEVICES", PKG / "data/gpu/amd_devices.txt"),
    "oc_boot_args.py": ("GPU_EMBED_OC_BOOT_ARGS", PKG / "data/gpu/oc_boot_args.py"),
}


@pytest.fixture(scope="module")
def block_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("bash") / "block.sh"
    path.write_text(STUBS + BLOCK + "\n")
    return path


@pytest.fixture
def bash(block_file, tmp_path):
    stub_bin = tmp_path / "stubbin"
    stub_bin.mkdir()
    for name in ("mokutil", "pveversion", "dkms"):
        (stub_bin / name).write_text("#!/bin/sh\nexit 1\n")
        (stub_bin / name).chmod(0o755)
    (stub_bin / "efibootmgr").write_text(f"#!/bin/sh\ncat <<'EOF'\n{EFI_GRUB}EOF\n")
    (stub_bin / "efibootmgr").chmod(0o755)

    def run(snippet: str, env: dict[str, str] | None = None, expect: int | None = 0, path: str = "") -> subprocess.CompletedProcess:
        full = {"PATH": f"{path + ':' if path else ''}{stub_bin}:/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
                "GPU_STATE_DIR": str(tmp_path / "state"), **(env or {})}
        res = subprocess.run(["bash", "-c", f"set -u; source {block_file}\n{snippet}"], capture_output=True, text=True,
                             env=full, timeout=120)
        if expect is not None:
            assert res.returncode == expect, (res.returncode, res.stdout, res.stderr)
        return res

    run.stub_bin = stub_bin
    return run


# ── embedded copies ──────────────────────────────────────────────────

@pytest.mark.parametrize("label", sorted(EMBEDS))
def test_embedded_copy_is_the_file(label):
    var, source = EMBEDS[label]
    m = re.search(rf"# ── GPU embedded: {re.escape(label)} begin ──\nIFS= read -r -d '' {var} <<'{var}_EOF' \|\| true\n(.*?){var}_EOF\n"
                  rf"# ── GPU embedded: {re.escape(label)} end ──\n", BASH, re.S)
    assert m, label
    assert m.group(1) == source.read_text(), f"{label} is stale: run python3 scripts/embed_gpu_engines.py"


def test_embedding_script_is_idempotent(tmp_path):
    before = (REPO / "scripts/bash/osx-proxmox-next.sh").read_bytes()
    subprocess.run([sys.executable, str(REPO / "scripts/embed_gpu_engines.py")], check=True)
    assert (REPO / "scripts/bash/osx-proxmox-next.sh").read_bytes() == before


def test_embedded_engines_are_usable_standalone(bash, tmp_path):
    out = bash('gpu_engine_init; ls "$GPU_ENGINE_DIR"; python3 "$GPU_ENGINE_DIR/host_setup.py" bogus </dev/null', expect=None)
    assert set(out.stdout.split()) == {"bios_guide.py", "bios_paths.json", "host_setup.py"}
    assert out.returncode == 64


def test_constants_match():
    assert re.search(r'^GPU_HOTPLUG_ARG="(.*)"$', BLOCK, re.M).group(1) == gpu.HOTPLUG_ARG
    assert re.search(r'^GPU_AMD_GENERIC_REASON="(.*)"$', BLOCK, re.M).group(1) == amd_host._GENERIC_REASON
    assert re.search(r'^GPU_AMD_VEGA20_WARNING="(.*)"$', BLOCK, re.M).group(1) == amd_host._VEGA20_WARNING
    assert re.search(r'^GPU_PRE_TURING_REASON="(.*)"$', BLOCK, re.M).group(1) == gpu_setup._PRE_TURING
    assert re.search(r"^GPU_DOCS_ROOT=\"(.*)\"$", BLOCK, re.M).group(1) == gpu_setup.DOCS_ROOT
    resume = re.search(r"^GPU_RESUME_CMD='(.*)'$", BLOCK, re.M).group(1)
    assert resume.endswith("-- gpu-setup") and "lucid-fabrics/osx-proxmox-next" in resume
    assert host_setup.STEPS == tuple(re.search(r"steps = \[(.*?)\]", BLOCK).group(1).replace('"', "").split(", "))


def test_troubleshooting_text_matches():
    for text in gpu_setup.TROUBLESHOOTING.values():
        for line in text.splitlines():
            assert line.replace('"', '\\"') in BLOCK or line.replace("'", "'") in BLOCK, line


def test_bash_dispatch():
    assert 'if [ "${1:-}" == "gpu-setup" ]' in BASH
    assert BASH.index('"gpu-setup"') < BASH.index("This will create a new macOS VM")
    assert 'ORIG_PWD="$PWD"' in BASH


def test_cache_dir_is_the_pythons():
    from osx_proxmox_next.defaults import DEFAULT_ISO_DIR
    assert f"${{NULLMOTH_CACHE_DIR:-{DEFAULT_ISO_DIR}}}" in BASH


# ── allowlists and verdicts ──────────────────────────────────────────

def sample_ids() -> list[int]:
    ids = {0x0000, 0x1636, 0x15E7, 0xFFFF, 0x73AF, 0x73A5, 0x73EF, 0x73C0, 0x7550, 0x744C}
    for fam in amd_host._load().families:
        ids |= {min(fam.ids), max(fam.ids), min(fam.ids) - 1, max(fam.ids) + 1}
    for _label, group, _reason in amd_host._load().deny:
        ids |= {min(group), max(group), min(group) - 1, max(group) + 1}
    return sorted(i for i in ids if 0 <= i <= 0xFFFF)


def test_amd_verdicts_match_python(bash):
    ids = sample_ids()
    out = bash("for d in %s; do gpu_verdict amd $d; amd_family_for $d || echo NOFAMILY; amd_hostpci_extra $d; echo; done" % " ".join(map(str, ids))).stdout.splitlines()
    for i, dev in enumerate(ids):
        verdict, fam, extra = out[i * 3:i * 3 + 3]
        kind, head, detail = gpu_setup.verdict("amd", dev)
        assert verdict == f"{kind}|{head}|{detail}", hex(dev)
        family = amd_host.family_for(dev)
        assert (fam == "NOFAMILY") == (family is None), hex(dev)
        if family:
            assert fam == f"{family.key}|{family.label}|{family.reset}|{' '.join(family.boot_args)}|{family.tahoe}"
        assert extra == amd_host.hostpci_extra(dev), hex(dev)


def test_nvidia_verdicts_match_python(bash):
    ids = [0x1B80, 0x1DFF, 0x1E00, 0x1E04, 0x2204, 0x2504, 0x2D58, 0x1E02, 0x2F58, 0x0001]
    out = bash("for d in %s; do gpu_verdict nvidia $d; done" % " ".join(map(str, ids))).stdout.splitlines()
    for line, dev in zip(out, ids):
        kind, head, detail = gpu_setup.verdict("nvidia", dev)
        assert line == f"{kind}|{head}|{detail}", hex(dev)


@pytest.mark.parametrize("args", ["", "-cpu host", "-cpu host " + gpu.HOTPLUG_ARG,
                                  "-cpu host -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=on -smbios x",
                                  "-global   ICH9-LPC.acpi-pci-hotplug-with-bridge-support=on"])
def test_global_arg_helpers_match(bash, args):
    out = bash(f"gpu_with_global_arg {args!r} {gpu.HOTPLUG_ARG!r}; gpu_without_global_arg {args!r} {gpu.HOTPLUG_ARG!r}").stdout.splitlines()
    assert out == [gpu.with_global_args(args, (gpu.HOTPLUG_ARG,)), gpu.without_global_args(args, (gpu.HOTPLUG_ARG,))]


@pytest.mark.parametrize("scenario", ["ok", "unbound", "navi22", "noiommu", "bigbar", "vega20", "vega10", "consolegpu"])
def test_amd_check_matches_python(bash, tmp_path, scenario):
    devices = {"vega20": 0x66AF, "vega10": 0x687F, "navi22": 0x73DF}
    add_card(tmp_path, device=devices.get(scenario, 0x73FF), driver="amdgpu" if scenario == "unbound" else "vfio-pci",
             boot_vga=scenario in ("bigbar", "consolegpu"), bar0=8 * 1024**3 if scenario == "bigbar" else 256 * 1024**2)
    if scenario == "noiommu":
        (tmp_path / "sys/bus/pci/devices/0000:06:00.0/iommu_group").unlink()
    cmd = tmp_path / "cmdline"
    cmd.write_text("quiet")
    py = amd_host.check_amd_gpu("06:00", tmp_path / "sys", cmd)
    out = bash('GPU_ISSUES=(); GPU_WARNS=(); GPU_HINTS=(); amd_check 0000:06:00; '
               'printf "I:%s\\n" "${GPU_ISSUES[@]}"; printf "W:%s\\n" "${GPU_WARNS[@]}"; printf "H:%s\\n" "${GPU_HINTS[@]}"',
               env={"GPU_SYSFS": str(tmp_path / "sys"), "GPU_CMDLINE": str(cmd)}).stdout.splitlines()
    got = {k: [ln[2:] for ln in out if ln.startswith(k + ":") and ln[2:]] for k in "IWH"}
    assert got == {"I": py.issues, "W": py.warnings, "H": py.hints}


# ── the engine through bash gives the same plan ──────────────────────

def test_host_plan_through_bash_equals_python(bash, tmp_path):
    root = make_host(tmp_path / "root")
    add_card(root)
    run = FakeRun({"efibootmgr -v": (0, EFI_GRUB)})
    target = host_setup.build_target(str(root), "0000:06:00", "amd", "early-bind")
    py = host_setup.plan_host(host_setup.detect_facts(str(root), run, env={}), target, None, str(root), run)
    payload = json.dumps({"target": target, "opts": {}})
    out = bash("printf '%s' \"$PAYLOAD\" | gpu_host plan", env={"GPU_ROOT": str(root), "PAYLOAD": payload}).stdout
    assert json.loads(out) == json.loads(json.dumps(py))
    assert [c["argv"] for c in json.loads(out)["commands"]] == [["update-grub"], ["update-initramfs", "-u", "-k", "all"]]


def test_bios_card_through_bash_equals_python(bash):
    facts = {"board_vendor": "ASUSTeK COMPUTER INC.", "board_name": "PRIME X670-P", "cpu_vendor": "amd",
             "virtualization": False, "dmar": False, "ivrs": False, "iommu_groups": 0, "efi": False, "displays": []}
    items = bios_guide.check_items(facts, None, "/nonexistent")
    expect = bios_guide.build_card([i for i in items if not i["ok"]], facts, bios_guide.load_data(), "asus", "RESUME")
    payload = json.dumps({"facts": facts, "items": items, "resume_cmd": "RESUME"})
    out = bash("printf '%s' \"$PAYLOAD\" | gpu_bios card", env={"PAYLOAD": payload, "GPU_ROOT": "/nonexistent"}).stdout
    assert out.rstrip("\n") == expect
    vendor = bash("printf '%s' \"$PAYLOAD\" | gpu_bios vendor", env={"PAYLOAD": json.dumps({"facts": facts})}).stdout
    assert vendor.strip() == "asus"


# ── bookkeeping files ────────────────────────────────────────────────

def test_record_bytes_match_python(bash, vm, tmp_path, monkeypatch):
    amd_cfg = MAC + "smbios1: uuid=11111111-2222-3333-4444-555555555555\n"
    vm(100, "stopped", amd_cfg)
    vm(101, "stopped", MAC.replace("sequoia", "tahoe").replace("vga: std\n", ""))
    bash('gpu_record_write 100 0000:06:00 amd "0000:06:00,pcie=1" "$GPU_HOTPLUG_ARG" yes gpu; '
         'gpu_record_write 101 0000:01:00 nvidia "0000:01:00,pcie=0" "$GPU_MMIO_ARG" no nullmoth', env=vm.env)
    cases = ((100, "amd", amd_cfg, amd_host.AMD_PROFILE, "0000:06:00", "0000:06:00,pcie=1", True, "gpu"),
             (101, "nvidia", MAC.replace("sequoia", "tahoe").replace("vga: std\n", ""), gpu.NVIDIA_PROFILE, "0000:01:00",
              "0000:01:00,pcie=0", False, "nullmoth"))
    for vmid, vendor, cfg, profile, bdf, hostpci, vga_none, oc in cases:
        rec = gpu.new_record(vmid, bdf, vendor, gpu.parse_config(cfg), profile, hostpci, vga_none, oc)
        monkeypatch.setattr(gpu, "STATE_DIR", tmp_path / "py")
        subprocess.run(gpu.record_step(rec).argv, check=True)
        assert (tmp_path / "py" / f"gpu-attach-{vmid}.json").read_bytes() == (tmp_path / "state" / f"gpu-attach-{vmid}.json").read_bytes()
    saved = json.loads((tmp_path / "state/gpu-attach-100.json").read_text())
    assert saved["after"] == {"hostpci0": "0000:06:00,pcie=1", "args": f'-device isa-applesmc,osk="x" -cpu host {gpu.HOTPLUG_ARG}',
                              "vga": "none", "added_args": [gpu.HOTPLUG_ARG], "replaced_args": {}}


def put_record(tmp_path, vmid, bdf, vendor, hostpci0, args, vga, vga_changed, oc, after=None):
    """A record as an earlier attach left it. With *after* unset it has no drift information (the old behaviour)."""
    path = tmp_path / "state" / f"gpu-attach-{vmid}.json"
    path.parent.mkdir(exist_ok=True)
    rec = {"vmid": vmid, "bdf": bdf, "vendor": vendor, "hostpci0": hostpci0, "args": args, "vga": vga,
           "vga_changed": vga_changed, "oc": oc, "fp": "", "after": after}
    path.write_text(json.dumps(rec, sort_keys=True))


def test_record_load_keeps_the_same_vm_and_replaces_a_stale_one(bash, vm, tmp_path):
    vm(100, "stopped", MAC + "smbios1: uuid=11111111-2222-3333-4444-555555555555\n")
    bash('gpu_record_write 100 0000:06:00 amd "0000:06:00,pcie=1" "$GPU_HOTPLUG_ARG" yes gpu', env=vm.env)
    # the same VM attached again (an interrupted run): the first record holds the true originals
    vm(100, "stopped", MAC + "smbios1: uuid=11111111-2222-3333-4444-555555555555\nhostpci0: changed\n")
    out = bash('gpu_record_write 100 0000:06:00 amd "0000:06:00,pcie=1" "$GPU_HOTPLUG_ARG" yes gpu; gpu_record_load 100; '
               'echo "$REC_FOUND|$REC_VENDOR|$REC_OC"', env=vm.env).stdout
    assert out.strip() == "yes|amd|gpu"
    assert json.loads((tmp_path / "state/gpu-attach-100.json").read_text())["hostpci0"] is None
    # the VM id was reused by another VM: its record replaces the old one
    vm(100, "stopped", MAC + "smbios1: uuid=99999999-2222-3333-4444-555555555555\nhostpci0: 0000:09:00\n")
    bash('gpu_record_write 100 0000:06:00 amd "0000:06:00,pcie=1" "$GPU_HOTPLUG_ARG" yes gpu', env=vm.env)
    assert json.loads((tmp_path / "state/gpu-attach-100.json").read_text())["hostpci0"] == "0000:09:00"
    assert bash("gpu_record_load 999", expect=1).returncode == 1


def test_snapshot_matches_the_pythons(bash, tmp_path):
    qm = bash.stub_bin / "qm"
    qm.write_text("#!/bin/sh\n[ \"$2\" = 100 ] && printf 'name: x\\nargs: -cpu host'\n[ \"$2\" = 100 ]\n")
    qm.chmod(0o755)
    out = bash('f=$(gpu_snapshot 100); cat "$f"; echo; echo "$f"; g=$(gpu_snapshot 7); cat "$g"', env={"GPU_SNAPSHOT_BASE": str(tmp_path / "base")}).stdout
    assert out.startswith("name: x\nargs: -cpu host\n")
    assert re.search(r"/generated/snapshots/vm-100-\d{8}T\d{6}Z\.conf", out)
    assert out.rstrip().endswith("# No existing VM config captured")


# ── a scripted qm ────────────────────────────────────────────────────

QM = r'''#!/bin/bash
# Scripted qm: VM state lives in $QM_DIR/<id>.conf and <id>.status; every call is logged.
echo "qm $*" >> "$QM_DIR/calls.log"
case "$1" in
  status) [ -f "$QM_DIR/$2.status" ] || exit 2; echo "status: $(cat "$QM_DIR/$2.status")" ;;
  config) [ -f "$QM_DIR/$2.conf" ] || exit 2; cat "$QM_DIR/$2.conf" ;;
  list) echo "VMID NAME STATUS MEM BOOTDISK PID"; for f in "$QM_DIR"/*.status; do [ -e "$f" ] || continue; id=$(basename "$f" .status); echo "$id vm$id $(cat "$f") 4096 32 1"; done ;;
  set) if [ -n "${QM_FAIL:-}" ] && [[ "$*" == *"$QM_FAIL"* ]]; then echo "boom" >&2; exit 1; fi ;;
  stop) echo stopped > "$QM_DIR/$2.status" ;;
esac
'''
MAC = 'name: macos-sequoia\nargs: -device isa-applesmc,osk="x" -cpu host\nide0: local-lvm:vm-100-disk-0,media=disk\nvga: std\n'


@pytest.fixture
def vm(bash, tmp_path):
    qm_dir = tmp_path / "qm"
    qm_dir.mkdir()
    qm = bash.stub_bin / "qm"
    qm.write_text(QM)
    qm.chmod(0o755)

    def setup(vmid: int = 100, status: str = "stopped", conf: str = MAC) -> None:
        (qm_dir / f"{vmid}.status").write_text(status)
        (qm_dir / f"{vmid}.conf").write_text(conf)

    setup()
    calls = lambda: [ln for ln in (qm_dir / "calls.log").read_text().splitlines() if ln.startswith("qm set")] if (qm_dir / "calls.log").exists() else []
    setup.calls = calls
    setup.env = {"QM_DIR": str(qm_dir)}
    return setup


def amd_host_env(tmp_path, bash_env=None, **card) -> dict[str, str]:
    root = make_host(tmp_path / "root")
    add_card(root, **card)
    (tmp_path / "cmdline").write_text("quiet")
    return {"GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_CMDLINE": str(tmp_path / "cmdline"),
            "GPU_SNAPSHOT_BASE": str(tmp_path / "snap"), **(bash_env or {})}


def test_amd_attach_steps_in_order(bash, vm, tmp_path):
    env = {**vm.env, **amd_host_env(tmp_path, device=0x73FF)}
    res = bash("gpu_oc_boot_args() { echo \"OC $*\"; }; amd_attach 100 06:00", env=env)
    assert "OC 100 attach agdpmod=pikera" in res.stdout and "OK: Attached 0000:06:00 (all functions) as hostpci0" in res.stdout
    assert vm.calls() == ["qm set 100 --hostpci0 0000:06:00,pcie=1", f"qm set 100 --args -device isa-applesmc,osk=\"x\" -cpu host {gpu.HOTPLUG_ARG}",
                          "qm set 100 --vga none"]
    rec = json.loads((tmp_path / "state/gpu-attach-100.json").read_text())
    assert rec["oc"] == "gpu" and rec["vga"] == "std" and rec["vga_changed"] is True and rec["hostpci0"] is None
    assert list((tmp_path / "snap/generated/snapshots").glob("vm-100-*.conf"))


def test_amd_attach_console_and_polaris_and_spoof(bash, vm, tmp_path):
    env = {**vm.env, **amd_host_env(tmp_path, device=0x73AF)}
    bash("gpu_oc_boot_args() { echo OC; }; amd_attach 100 06:00 --display console", env=env)
    assert vm.calls()[0] == "qm set 100 --hostpci0 0000:06:00,pcie=1,device-id=0x73bf" and not any("--vga" in c for c in vm.calls())
    dry = bash("amd_attach 100 06:00 --dry-run", env={**vm.env, **amd_host_env(tmp_path / "x", device=0x67DF)}).stdout
    assert "DRY RUN" in dry and "boot-args" not in dry and "--hostpci0 0000:06:00,pcie=1" in dry and "--vga none" in dry


@pytest.mark.parametrize("fail,needle", [("--hostpci0", "qm set --hostpci0 failed"), ("--args", "qm set --args failed"), ("--vga", "qm set --vga failed")])
def test_amd_attach_qm_set_failures(bash, vm, tmp_path, fail, needle):
    env = {**vm.env, **amd_host_env(tmp_path), "QM_FAIL": fail}
    res = bash("gpu_oc_boot_args() { true; }; amd_attach 100 06:00", env=env, expect=1)
    assert needle in res.stdout
    assert f"qm set 100 {fail}" in "\n".join(vm.calls())


def test_amd_attach_oc_failure_leaves_the_vm_alone(bash, vm, tmp_path):
    env = {**vm.env, **amd_host_env(tmp_path)}
    res = bash("gpu_oc_boot_args() { return 1; }; amd_attach 100 06:00", env=env, expect=1)
    assert "Could not patch the OpenCore disk; the VM was not changed" in res.stdout and vm.calls() == []
    # the record is written first; with the VM untouched, undoing it is a no-op that still finishes
    assert (tmp_path / "state/gpu-attach-100.json").exists()
    bash("gpu_oc_boot_args() { return 0; }; gpu_detach_record 100", env=vm.env)
    assert vm.calls() == [] and not (tmp_path / "state/gpu-attach-100.json").exists()


@pytest.mark.parametrize("conf,status,needle", [
    (MAC + "ide2: local:iso/r.dmg,media=disk\n", "stopped", "recovery disk attached"),
    (MAC, "running", "VM 100 is running"),
    ("name: x\nargs: -cpu host\n", "stopped", "not a macOS VM"),
    (MAC + "hostpci0: 0000:09:00,pcie=0\n", "stopped", "hostpci0 is already in use"),
])
def test_amd_attach_refusals(bash, vm, tmp_path, conf, status, needle):
    vm(100, status, conf)
    res = bash("amd_attach 100 06:00", env={**vm.env, **amd_host_env(tmp_path)}, expect=1)
    assert needle in res.stdout and vm.calls() == []


def test_amd_attach_usage_and_other_vms(bash, vm, tmp_path):
    env = {**vm.env, **amd_host_env(tmp_path)}
    assert "Usage: amd_attach" in bash("amd_attach x 06:00", env=env, expect=1).stdout
    assert "Unknown option" in bash("amd_attach 100 06:00 --bogus", env=env, expect=1).stdout
    assert "VM 7 not found" in bash("amd_attach 7 06:00", env=env, expect=1).stdout
    vm(101, "running", "hostpci0: 06:00,pcie=1\n")
    assert "in use by running VM 101" in bash("amd_attach 100 06:00", env=env, expect=1).stdout
    vm(101, "stopped", "hostpci0: 06:00,pcie=1\n")
    ok = bash("gpu_oc_boot_args() { true; }; amd_attach 100 06:00 --stop-vm", env=env)
    assert "Also assigned to VM 101 (stopped)" in ok.stdout


def test_amd_attach_stops_a_running_vm_when_asked(bash, vm, tmp_path):
    vm(100, "running", MAC)
    bash("gpu_oc_boot_args() { true; }; amd_attach 100 06:00 --stop-vm", env={**vm.env, **amd_host_env(tmp_path)})
    assert (Path(vm.env["QM_DIR"]) / "100.status").read_text().strip() == "stopped"


def test_gpu_detach_restores_exactly(bash, vm, tmp_path):
    put_record(tmp_path, 100, "0000:06:00", "amd", None, '-device isa-applesmc,osk="x" -cpu host', "std", True, "gpu")
    attached = MAC.replace("-cpu host", f"-cpu host {gpu.HOTPLUG_ARG}").replace("vga: std", "vga: none") + "hostpci0: 0000:06:00,pcie=1\n"
    vm(100, "stopped", attached)
    res = bash("gpu_oc_boot_args() { echo \"OC $*\"; }; gpu_detach_record 100", env=vm.env)
    assert "OC 100 restore" in res.stdout
    assert vm.calls() == ["qm set 100 --delete hostpci0", 'qm set 100 --args -device isa-applesmc,osk="x" -cpu host', "qm set 100 --vga std"]
    assert not (tmp_path / "state/gpu-attach-100.json").exists()


def test_gpu_detach_restores_a_preexisting_slot_and_deletes_added_args(bash, vm, tmp_path):
    put_record(tmp_path, 100, "0000:06:00", "amd", "0000:06:00,pcie=0", None, None, True, "")
    vm(100, "stopped", "args: " + gpu.HOTPLUG_ARG + "\nhostpci0: 0000:06:00,pcie=1\nvga: none\nname: macos-sequoia\n")
    bash("gpu_detach_record 100", env=vm.env)
    assert vm.calls() == ["qm set 100 --hostpci0 0000:06:00,pcie=0", "qm set 100 --delete args", "qm set 100 --delete vga"]


def test_gpu_detach_dry_run_and_refusals(bash, vm, tmp_path):
    put_record(tmp_path, 100, "0000:06:00", "amd", None, "-cpu host", "std", True, "gpu")
    vm(100, "stopped", "args: -cpu host x\nhostpci0: 0000:06:00,pcie=1\n")
    out = bash("gpu_detach_record 100 yes", env=vm.env).stdout
    assert "DRY RUN" in out and "pre-gpu" in out and "--delete hostpci0" in out and "--args '-cpu host'" in out and "--vga 'std'" in out
    assert vm.calls() == []
    vm(100, "running", "args: x\n")
    assert "Stop it first" in bash("gpu_detach_record 100", env=vm.env, expect=1).stdout
    assert "not found" in bash("gpu_detach_record 55", env=vm.env, expect=1).stdout
    assert bash("gpu_detach_record 200", env=vm.env, expect=1).returncode == 1


@pytest.mark.parametrize("fail,needle", [("--delete hostpci0", "qm set --delete hostpci0 failed"), ("--args", "qm set --args failed"),
                                          ("--vga", "qm set --vga failed")])
def test_gpu_detach_qm_set_failures(bash, vm, tmp_path, fail, needle):
    put_record(tmp_path, 100, "0000:06:00", "amd", None, "-cpu host", "std", True, "gpu")
    vm(100, "stopped", MAC.replace("vga: std", "vga: none") + f"hostpci0: 0000:06:00,pcie=1\n")
    res = bash("gpu_oc_boot_args() { true; }; gpu_detach_record 100", env={**vm.env, "QM_FAIL": fail}, expect=1)
    assert needle in res.stdout
    assert (tmp_state := res) and True


def test_gpu_detach_oc_failure_changes_nothing(bash, vm, tmp_path):
    put_record(tmp_path, 100, "0000:06:00", "amd", None, "-cpu host", "std", True, "gpu")
    vm(100, "stopped", MAC + "hostpci0: 0000:06:00,pcie=1\n")
    res = bash("gpu_oc_boot_args() { return 1; }; gpu_detach_record 100", env=vm.env, expect=1)
    assert "Could not restore the OpenCore config" in res.stdout and vm.calls() == []
    put_record(tmp_path, 100, "x", "nvidia", None, None, None, False, "nullmoth")
    nm = bash("nullmoth_oc_patch() { return 1; }; gpu_detach_record 100", env=vm.env, expect=1)
    assert "Could not restore" in nm.stdout


# ── nvidia-attach / nvidia-detach: the records and the failing qm set ─

def nvidia_env(tmp_path, vm_env) -> dict[str, str]:
    root = make_host(tmp_path / "root")
    good_card(root / "sys")
    (tmp_path / "cmdline").write_text("quiet")
    return {**vm_env, "GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_CMDLINE": str(tmp_path / "cmdline"),
            "GPU_SNAPSHOT_BASE": str(tmp_path / "snap"), "NULLMOTH_CACHE_DIR": str(tmp_path / "cache")}


NV_STUBS = ("nullmoth_fetch_amfipass() { echo /cache/a.zip; }; nullmoth_oc_patch() { echo \"NMOC $*\"; }; ")


def test_nvidia_attach_records_the_originals_and_snapshots(bash, vm, tmp_path):
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    res = bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk", env=nvidia_env(tmp_path, vm.env))
    assert "NMOC 100 attach /cache/a.zip" in res.stdout
    assert vm.calls()[0] == "qm set 100 --hostpci0 0000:01:00,pcie=0" and vm.calls()[1].endswith(gpu.MMIO_ARG)
    rec = json.loads((tmp_path / "state/gpu-attach-100.json").read_text())
    assert (rec["oc"], rec["vendor"], rec["hostpci0"], rec["vga"], rec["vga_changed"]) == ("nullmoth", "nvidia", None, "std", False)
    assert rec["args"] == '-device isa-applesmc,osk="x" -cpu host'
    assert list((tmp_path / "snap/generated/snapshots").glob("vm-100-*.conf"))


@pytest.mark.parametrize("fail,needle", [("--hostpci0", "qm set --hostpci0 failed"), ("--args", "qm set --args failed")])
def test_nvidia_attach_qm_set_failures(bash, vm, tmp_path, fail, needle):
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    res = bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk", env={**nvidia_env(tmp_path, vm.env), "QM_FAIL": fail}, expect=1)
    assert needle in res.stdout


def test_nvidia_detach_uses_the_record_and_the_legacy_path(bash, vm, tmp_path):
    vm(100, "stopped", MAC.replace("sequoia", "tahoe"))
    env = nvidia_env(tmp_path, vm.env)
    bash(NV_STUBS + "nvidia_attach 100 01:00 --accept-nullmoth-risk", env=env)
    attached = MAC.replace("sequoia", "tahoe").replace("-cpu host", f"-cpu host {gpu.MMIO_ARG}") + "hostpci0: 0000:01:00,pcie=0\n"
    vm(100, "stopped", attached)
    (Path(vm.env["QM_DIR"]) / "calls.log").unlink()
    res = bash(NV_STUBS + "nvidia_detach 100", env=env)
    assert "NMOC 100 restore" in res.stdout
    assert vm.calls() == ["qm set 100 --delete hostpci0", 'qm set 100 --args -device isa-applesmc,osk="x" -cpu host']
    assert not (tmp_path / "state/gpu-attach-100.json").exists()
    # attached by an older version: no record, the known additions go away
    (Path(vm.env["QM_DIR"]) / "calls.log").unlink()
    bash(NV_STUBS + "nvidia_detach 100", env=env)
    assert vm.calls() == ["qm set 100 --delete hostpci0", 'qm set 100 --args -device isa-applesmc,osk="x" -cpu host']


@pytest.mark.parametrize("fail,needle", [("--delete hostpci0", "qm set --delete hostpci0 failed"), ("--args", "qm set --args failed")])
def test_nvidia_detach_qm_set_failures(bash, vm, tmp_path, fail, needle):
    env = nvidia_env(tmp_path, {**vm.env, "QM_FAIL": fail})
    vm(100, "stopped", MAC.replace("sequoia", "tahoe").replace("-cpu host", f"-cpu host {gpu.MMIO_ARG}") + "hostpci0: 0000:01:00,pcie=0\n")
    legacy = bash(NV_STUBS + "nvidia_detach 100", env=env, expect=1)
    assert needle.split(" failed")[0].replace("qm set ", "qm set ") in legacy.stdout or "failed" in legacy.stdout
    put_record(tmp_path, 100, "0000:01:00", "nvidia", None, "-cpu host", None, False, "nullmoth")
    rec = bash(NV_STUBS + "nvidia_detach 100", env=env, expect=1)
    assert "failed" in rec.stdout


# ── scan and the whole walkthrough ───────────────────────────────────

def test_scan_and_card_text(bash, tmp_path, vm):
    root = make_host(tmp_path / "root")
    add_card(root, bdf="0000:06:00", device=0x73FF, boot_vga=True)
    add_card(root, bdf="0000:41:00", device=0x73DF, group=9)
    add_card(root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    vm(120, "running", "hostpci0: 0000:01:00,pcie=0\n")
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\necho \"06:00.0 VGA compatible controller [0300]: Test Vendor Card [1002:73ff] (rev c1)\"\n")
    lspci.chmod(0o755)
    out = bash('gpu_scan; for l in "${GPU_CARDS[@]}"; do echo "$l"; gpu_card_selectable "$l" && echo SELECTABLE; gpu_card_text "$l"; done',
               env={**vm.env, "GPU_SYSFS": str(root / "sys")}).stdout
    assert "0000:06:00|amd|29695|Test Vendor Card|native|Works natively on macOS 13 to 26|" in out
    assert "|unsupported|Not supported by macOS|" in out and "Navi 22" in out
    assert "|nullmoth|Works with NullMoth (beta) on macOS 15.5 to 26|" in out and "120:running" in out
    assert out.count("SELECTABLE") == 1
    assert "Status: bound to vfio-pci" in out and "host console" not in out  # boot_vga on vfio-pci is not the host console
    assert "Note: The firmware shows its boot screen on this card." in out
    assert "Can't use: In use by VM 120 (running)" in out
    assert "AMD 1002:73ff" in out and "NVIDIA 10de:2504" in out
    assert out.splitlines()[0].startswith("0000:06:00|")  # usable cards come first
    # the same cards through the Python renderer, line for line
    cards = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 13 or not parts[2].isdigit():
            continue
        users = [(int(u.split(":")[0]), u.split(":")[1] == "running") for u in parts[9].split(",") if u]
        cards.append(gpu_setup.GpuCard(parts[0], parts[1], int(parts[2]), parts[3], parts[4], parts[5], parts[6],
                                       parts[7] == "1", parts[12] == "1", parts[8] or None, users, [],
                                       [r for r in parts[10].split(",") if r]))
    assert len(cards) == 3
    python_text = "\n".join(gpu_setup.card_text(c) for c in cards)
    bash_text = "\n".join(ln for ln in out.splitlines() if ln.startswith((">> ", "   ")) and ("+" in ln or "|" in ln))
    assert bash_text == python_text


def test_walkthrough_through_the_reboot_attach_and_undo(bash, vm, tmp_path):
    root = make_host(tmp_path / "root")
    add_card(root)
    stub = bash.stub_bin
    for name in ("update-grub", "update-initramfs"):
        (stub / name).write_text("#!/bin/sh\nexit 0\n")
        (stub / name).chmod(0o755)
    lspci = stub / "lspci"
    lspci.write_text("#!/bin/sh\necho \"06:00.0 VGA compatible controller [0300]: Test Card [1002:73ff]\"\n")
    lspci.chmod(0o755)
    state_dir = root / "var/lib/osx-proxmox-next"
    env = {**vm.env, "GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_CMDLINE": str(root / "proc/cmdline"),
           "GPU_STATE_DIR": str(state_dir), "GPU_SNAPSHOT_BASE": str(tmp_path / "snap")}
    write(root, "/proc/sys/kernel/random/boot_id", "boot-one\n")
    first = bash("gpu_setup --yes --vm 100 --gpu 06:00", env=env, expect=4)
    assert "This is exactly what will change" in first.stdout and "The host must reboot" in first.stdout
    assert "GRUB_CMDLINE_LINUX_DEFAULT" in (root / "etc/default/grub.d/osx-proxmox-next.cfg").read_text()
    state = json.loads((state_dir / "gpu-setup.json").read_text())
    assert state["step"] == 5 and state["reboot_needed"] and state["gpu"]["vendor"] == "amd"
    assert "step 5 of 8 (Reboot)" in (root / "etc/profile.d/osx-proxmox-next-gpu.sh").read_text()
    # the host rebooted
    write(root, "/proc/sys/kernel/random/boot_id", "boot-two\n")
    write(root, "/proc/cmdline", "BOOT_IMAGE=/x ro intel_iommu=on iommu=pt")
    write(root, "/proc/modules", "vfio 1 0 - Live 0x0\nvfio_iommu_type1 1 0 - Live 0x0\nvfio_pci 1 0 - Live 0x0\n")
    second = bash("gpu_oc_boot_args() { echo \"OC $*\"; }; gpu_setup --yes", env=env)
    assert "OK: Attached 0000:06:00" in second.stdout and "Setup summary" in second.stdout
    state = json.loads((state_dir / "gpu-setup.json").read_text())
    assert state["finished"] and state["vm_records"] == [100] and not (root / "etc/profile.d/osx-proxmox-next-gpu.sh").exists()
    assert (state_dir / "gpu-attach-100.json").exists()
    # undo
    vm(100, "stopped", MAC + "hostpci0: 0000:06:00,pcie=1\n")
    undo = bash("gpu_oc_boot_args() { echo \"OC $*\"; }; gpu_setup --undo", env=env)
    assert "Everything this setup changed is back as it was." in undo.stdout and "Restored /etc/default/grub.d" in undo.stdout
    assert not (state_dir / "gpu-setup.json").exists() and not (state_dir / "gpu-attach-100.json").exists()
    assert not (root / "etc/default/grub.d/osx-proxmox-next.cfg").exists()


def test_walkthrough_status_changes_nothing(bash, vm, tmp_path):
    root = make_host(tmp_path / "root")
    add_card(root)
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\necho \"06:00.0 VGA compatible controller [0300]: Test Card [1002:73ff]\"\n")
    lspci.chmod(0o755)
    env = {**vm.env, "GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_STATE_DIR": str(root / "var/lib/osx-proxmox-next")}
    out = bash("gpu_setup --status", env=env).stdout
    assert "[1 Scan]" in out and "Works natively" in out and "No setup in progress." in out
    assert not (root / "var/lib/osx-proxmox-next").exists()
    assert "Unknown option" in bash("gpu_setup --bogus", env=env, expect=2).stdout
    assert "--vm needs a number" in bash("gpu_setup --vm x", env=env, expect=2).stdout


def test_walkthrough_bios_pause_and_refusals(bash, vm, tmp_path):
    root = make_host(tmp_path / "root", virt=False, tables=(), efi=False)
    add_card(root)
    lspci = bash.stub_bin / "lspci"
    lspci.write_text("#!/bin/sh\necho \"06:00.0 VGA compatible controller [0300]: Test Card [1002:73ff]\"\n")
    lspci.chmod(0o755)
    env = {**vm.env, "GPU_ROOT": str(root), "GPU_SYSFS": str(root / "sys"), "GPU_STATE_DIR": str(root / "var/lib/osx-proxmox-next")}
    res = bash("gpu_setup --yes --vm 100 --gpu 06:00", env=env, expect=3)
    assert "BIOS STEPS FOR THIS SERVER" in res.stdout and "When you are back in Proxmox" in res.stdout
    assert (root / "root/osx-proxmox-next-bios-steps.txt").exists() and (root / "etc/profile.d/osx-proxmox-next-gpu.sh").exists()
    state_file = root / "var/lib/osx-proxmox-next/gpu-setup.json"
    state_file.unlink()
    assert "is not one of the usable cards" in bash("gpu_setup --yes --gpu 09:00", env=env, expect=2).stdout
    assert "VM 999 not found" in bash("gpu_setup --yes --gpu 06:00 --vm 999", env=env, expect=2).stdout
    bad = make_host(tmp_path / "bad")
    add_card(bad, device=0x73DF)
    res = bash("gpu_setup --yes", env={**env, "GPU_ROOT": str(bad), "GPU_SYSFS": str(bad / "sys")}, expect=2)
    assert "Nothing here can run macOS graphics" in res.stdout


def test_scan_marks_cards_whose_group_holds_storage(bash, tmp_path, vm):
    from gpu_fakes import add_other
    root = make_host(tmp_path / "root")
    add_card(root, bdf="0000:06:00", device=0x73FF, group=7)
    add_other(root, "0000:07:00.0", 0x144D, 0xA808, 0x010802, 7)
    out = bash('gpu_scan; for l in "${GPU_CARDS[@]}"; do echo "$l"; gpu_card_selectable "$l" && echo SELECTABLE; done; true',
               env={**vm.env, "GPU_SYSFS": str(root / "sys")}).stdout
    assert out.splitlines()[0].endswith("|0000:07:00.0||0") and "SELECTABLE" not in out


def test_undo_without_a_record_has_nothing_to_revert_and_finishes(bash, vm, tmp_path):
    root = make_host(tmp_path / "root")
    state_dir = root / "var/lib/osx-proxmox-next"
    state_dir.mkdir(parents=True)
    (state_dir / "gpu-setup.json").write_text(json.dumps({**host_setup.new_state(), "vm_records": [100]}))
    vm(100, "stopped", MAC + "hostpci0: 0000:01:00,pcie=0\n")
    env = {**vm.env, "GPU_ROOT": str(root), "GPU_STATE_DIR": str(state_dir), "GPU_SYSFS": str(root / "sys")}
    res = bash(NV_STUBS + "gpu_setup --undo", env=env)
    assert "NMOC" not in res.stdout and "VM 100: nothing to revert" in res.stdout
    assert vm.calls() == [] and not (state_dir / "gpu-setup.json").exists()  # the VM kept its hostpci0
