"""Python/bash parity for the NullMoth NVIDIA beta (the repo's parity rule)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from nullmoth_fakes import add_dev, good_card
from osx_proxmox_next import gpu, nullmoth, nvidia_host, oc_builder

REPO = Path(__file__).resolve().parent.parent
BASH = (REPO / "scripts/bash/osx-proxmox-next.sh").read_text()
PATCHER = (REPO / "src/osx_proxmox_next/data/nullmoth/oc_patch.py").read_text()
BLOCK = BASH[BASH.index("# ── Vendor-neutral GPU helpers begin ──"):
             BASH.index("# ── NullMoth NVIDIA GPU attach (beta) end ──")]


def var(name: str) -> str:
    m = re.search(rf'^{name}=(["\'])(.*?)\1$', BLOCK, re.M)
    assert m, name
    return m.group(2)


def run_bash(snippet: str, env: dict[str, str] | None = None) -> str:
    stubs = "BOLD='';CL='';CROSS='';INFO='';YW='';RD='';GN='';BFR='';CM='';TAB='';HOLD='';"
    res = subprocess.run(["bash", "-c", f"set -u; {stubs}\n{BLOCK}\n{snippet}"], capture_output=True,
                         text=True, env={"PATH": "/usr/bin:/bin", **(env or {})})
    assert res.returncode == 0, res.stderr
    return res.stdout


def test_notice_and_ack_copy_match():
    m = re.search(r"<<'NULLMOTH_NOTICE' \|\| true\n(.*?)\nNULLMOTH_NOTICE\n", BLOCK, re.S)
    assert m.group(1) == nullmoth.NOTICE_BODY
    assert var("NULLMOTH_NOTICE_TITLE") == nullmoth.NOTICE_TITLE
    assert var("NULLMOTH_ACK_LABEL") == nullmoth.ACK_LABEL
    assert var("NULLMOTH_ACK_PHRASE") == nullmoth.ACK_PHRASE
    assert var("NULLMOTH_URL") == nullmoth.NULLMOTH_URL
    assert var("GPU_MMIO_ARG") == gpu.MMIO_ARG


def test_amfipass_pin_matches():
    assert var("NULLMOTH_AMFIPASS_URL") == oc_builder.AMFIPASS.url
    assert var("NULLMOTH_AMFIPASS_SHA256") == oc_builder.AMFIPASS.sha256


def test_allowlist_matches_data_file():
    m = re.search(r'^NULLMOTH_SUPPORTED_IDS="\n(.*?)\n"$', BLOCK, re.M | re.S)
    assert {int(x, 16) for x in m.group(1).split()} == set(nvidia_host.supported_device_ids())


def test_embedded_patcher_is_the_data_file():
    m = re.search(r"<<'NULLMOTH_PY'\n(.*?)\nNULLMOTH_PY\n", BLOCK, re.S)
    assert m.group(1) + "\n" == PATCHER
    assert nullmoth._oc_patch_source() == PATCHER


def test_next_steps_match():
    for step in nullmoth.NEXT_STEPS:
        words = step.split(" ")[:6]
        assert " ".join(words) in BLOCK, step


def test_bar_note_matches_in_both():
    assert nullmoth._BAR_NOTE.strip() in BLOCK


def test_bash_dispatch_and_commands_exist():
    assert 'if [ "${1:-}" == "nvidia-attach" ]' in BASH and 'if [ "${1:-}" == "nvidia-detach" ]' in BASH
    assert BASH.index('"nvidia-attach"') < BASH.index("This will create a new macOS VM")
    for flag in ("--accept-nullmoth-risk", "--dry-run", "--macos"):
        assert flag in BLOCK


@pytest.mark.parametrize("raw", ["01:00", "0000:01:00.0", "0A:1F.3", "zz", "1:00", "01:00.9"])
def test_normalize_bdf_matches(raw):
    out = run_bash(f'gpu_normalize_bdf {raw!r} || echo NONE')
    assert out.strip() == (gpu.normalize_bdf(raw) or "NONE")


@pytest.mark.parametrize("args", [
    '-device isa-applesmc,osk="x" -cpu host',
    '-cpu host -global q35-pcihost.pci-hole64-size=32G -smbios type=2',
    '-cpu host ' + gpu.MMIO_ARG,
    "",
])
def test_mmio_helpers_match(args):
    out = run_bash(f'gpu_with_mmio_arg {args!r}; gpu_without_mmio_arg {args!r}').splitlines()
    assert out == [gpu.with_mmio_arg(args), gpu.without_mmio_arg(args)]


@pytest.mark.parametrize("scenario", ["ok", "unbound", "old", "foreign", "noiommu", "bigbar"])
def test_gpu_check_matches_python(tmp_path, scenario):
    V = 0x10DE
    if scenario == "old":
        add_dev(tmp_path, "0000:01:00.0", V, 0x1B80)
    elif scenario == "unbound":
        add_dev(tmp_path, "0000:01:00.0", V, 0x2504, driver="nouveau")
        add_dev(tmp_path, "0000:01:00.2", V, 0x1AD8, cls=0x0C0330, driver=None)
    else:
        good_card(tmp_path, boot_vga=scenario == "bigbar", bar1=(8 * 1024**3 if scenario == "bigbar" else 2**28))
    if scenario == "foreign":
        add_dev(tmp_path, "0000:00:1f.3", 0x8086, 0x1234, cls=0x040300, group=1)
    if scenario == "noiommu":
        (tmp_path / "bus/pci/devices/0000:01:00.0/iommu_group").unlink()
    cmd = tmp_path / "cmdline"
    cmd.write_text("quiet")
    py = nvidia_host.check_nvidia_gpu("01:00", tmp_path, cmd)
    out = run_bash(
        'gpu_check 0000:01:00 4318 nullmoth_supported_device "Device 10de:%s is older than Turing. '
        'NullMoth supports RTX 20 series and newer only."\n'
        'printf "%s\\n" "${GPU_ISSUES[@]:-}" "--" "${GPU_WARNS[@]:-}" "--" "${GPU_HINTS[@]:-}"',
        {"GPU_SYSFS": str(tmp_path), "GPU_CMDLINE": str(cmd)},
    ).split("\n--\n")
    clean = lambda t: [x for x in t.strip("\n").split("\n") if x]  # noqa: E731
    assert clean(out[0]) == py.issues
    assert clean(out[1]) == py.warnings
    assert clean(out[2]) == py.hints
    assert (scenario == "ok") == (py.ok and not py.warnings)


def test_detect_macos_matches():
    cfg = "name: macos-tahoe\ndescription: %3Ch2%3EmacOS%20Sequoia%2015%20VM\n"
    out = run_bash('nullmoth_detect_macos "$CFG"', {"CFG": cfg})
    assert out.strip() == nullmoth.detect_macos({"name": "macos-tahoe", "description": "macOS Sequoia 15 VM"})
    assert subprocess.run(["bash", "-c", f"set -u; {BLOCK}\nnullmoth_detect_macos 'name: vm'"],
                          capture_output=True, text=True).returncode == 1


def test_bash_acknowledge_without_tty_refuses():
    out = run_bash('nullmoth_acknowledge no < /dev/null && echo ACCEPTED || echo REFUSED')
    assert "NVIDIA GPU support (Beta)" in out and out.strip().endswith("REFUSED")
    assert run_bash('nullmoth_acknowledge yes && echo ACCEPTED').strip().endswith("ACCEPTED")


def test_bash_patcher_executes_like_python(tmp_path):
    from nullmoth_fakes import write_oc
    import plistlib, zipfile, importlib.util
    spec = importlib.util.spec_from_file_location("oc_patch", REPO / "src/osx_proxmox_next/data/nullmoth/oc_patch.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("AMFIPass.kext/Contents/Info.plist", "x")
    oc = tmp_path / "bash/OC"; write_oc(oc)
    script = re.search(r"<<'NULLMOTH_PY'\n(.*?)\nNULLMOTH_PY\n", BLOCK, re.S).group(1)
    res = subprocess.run(["python3", "-", str(oc), "attach"], input=script, text=True, capture_output=True,
                         env={"AMFIPASS_ZIP": str(z), "PATH": "/usr/bin:/bin"})
    assert res.returncode == 0, res.stderr
    oc2 = tmp_path / "py/OC"; write_oc(oc2)
    import os
    os.environ["AMFIPASS_ZIP"] = str(z)
    try:
        mod.attach(str(oc2))
    finally:
        del os.environ["AMFIPASS_ZIP"]
    assert plistlib.loads((oc / "config.plist").read_bytes()) == plistlib.loads((oc2 / "config.plist").read_bytes())
