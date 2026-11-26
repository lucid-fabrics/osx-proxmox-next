"""The Mac-free install verb: the dry run writes nothing, bad inputs are named, and the plan shows the QEMU command."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "arm64" / "snapintosh"
ECID = "4533301000242790675"


def run(tmp_path, args, extra_env=None):
    env = dict(os.environ, SNAPINTOSH_HOME=str(tmp_path / "home"), XDG_CONFIG_HOME=str(tmp_path / "cfg"),
               HOME=str(tmp_path))
    env.update(extra_env or {})
    return subprocess.run(["bash", str(CLI), *args], cwd=tmp_path, env=env, capture_output=True, text=True)


@pytest.fixture
def inputs(tmp_path):
    ipsw = tmp_path / "a.ipsw"
    ipsw.write_text("ipsw")
    aux = tmp_path / "aux.img"
    aux.write_text("aux")
    return ipsw, aux


def dry(tmp_path, inputs, *extra):
    ipsw, aux = inputs
    return run(tmp_path, ["install", "--dry-run", "--ipsw", str(ipsw), "--aux", str(aux),
                          "--ecid", ECID, "--disk", str(tmp_path / "d.raw"), *extra])


def test_the_dry_run_prints_the_plan_and_the_qemu_command_and_writes_nothing(tmp_path, inputs):
    (tmp_path / "cwd").mkdir()
    before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    result = dry(tmp_path, inputs)
    assert result.returncode == 0, result.stderr
    assert "restore plan (dry run, nothing is written)" in result.stdout
    assert "vmapple-virtio-usb-pci" in result.stdout
    assert r"memory-backend-memfd\,id=ram0\,size=8G\,share=on" in result.stdout
    assert f"uuid={ECID}" in result.stdout
    assert "restore.cpio" in result.stdout and "restore-ramdisk.im4p" in result.stdout
    after = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    assert after == before
    assert not (tmp_path / "home").exists()   # no run dir, no tss cache, no ensure_home


def test_a_bad_ecid_is_named_and_exits_2(tmp_path, inputs):
    ipsw, aux = inputs
    result = run(tmp_path, ["install", "--dry-run", "--ipsw", str(ipsw), "--aux", str(aux),
                            "--ecid", "12x", "--disk", str(tmp_path / "d.raw")])
    assert result.returncode == 2 and "ECID" in result.stderr


def test_an_ecid_of_2_to_the_64_is_refused_and_the_maximum_64_bit_value_is_accepted(tmp_path, inputs):
    ipsw, aux = inputs
    over = run(tmp_path, ["install", "--dry-run", "--ipsw", str(ipsw), "--aux", str(aux),
                          "--ecid", str(2 ** 64), "--disk", str(tmp_path / "d.raw")])
    assert over.returncode == 2 and "ECID" in over.stderr
    top = run(tmp_path, ["install", "--dry-run", "--ipsw", str(ipsw), "--aux", str(aux),
                         "--ecid", str(2 ** 64 - 1), "--disk", str(tmp_path / "d.raw")])
    assert top.returncode == 0, top.stderr
    assert f"uuid={2 ** 64 - 1}" in top.stdout


def test_a_relative_disk_is_refused(tmp_path, inputs):
    ipsw, aux = inputs
    result = run(tmp_path, ["install", "--dry-run", "--ipsw", str(ipsw), "--aux", str(aux),
                            "--ecid", ECID, "--disk", "relative.raw"])
    assert result.returncode == 2 and "disk" in result.stderr.lower()


def test_a_disk_that_is_a_directory_is_refused(tmp_path, inputs):
    directory = tmp_path / "disk"
    directory.mkdir()
    result = dry(tmp_path, inputs, "--disk", str(directory))
    assert result.returncode == 2 and "directory" in result.stderr


def test_a_missing_ipsw_is_an_error_not_a_traceback(tmp_path, inputs):
    _, aux = inputs
    result = run(tmp_path, ["install", "--dry-run", "--ipsw", str(tmp_path / "nope.ipsw"),
                            "--aux", str(aux), "--ecid", ECID, "--disk", str(tmp_path / "d.raw")])
    assert result.returncode == 2 and "no such IPSW" in result.stderr


def test_an_unknown_option_is_named(tmp_path, inputs):
    result = dry(tmp_path, inputs, "--bogus")
    assert result.returncode == 2 and "--bogus" in result.stderr


def test_a_real_run_gives_qemu_the_usb_chardev_exactly_once(tmp_path, inputs):
    """go.sh adds the vusb chardev itself; install adding it too made QEMU die with Duplicate ID 'vusb'."""
    ipsw, aux = inputs
    qemu, args_file = tmp_path / "qemu", tmp_path / "qemu.args"
    qemu.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {args_file}\n")
    qemu.chmod(0o755)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("restore.cpio", "restore-ramdisk.im4p", "ambrosia-r4.bin"):
        (bundle / name).write_text("x")
    run(tmp_path, ["install", "--ipsw", str(ipsw), "--aux", str(aux), "--ecid", ECID, "--disk", str(tmp_path / "d.raw")],
        {"QEMU": str(qemu), "SNAPINTOSH_INSTALL_BUNDLE": str(bundle)})
    lines = args_file.read_text().splitlines()
    assert sum(1 for line in lines if line.startswith("socket,id=vusb,")) == 1
    assert sum(1 for line in lines if line == "vmapple-virtio-usb-pci,chardev=vusb") == 1


def test_a_relative_cache_dir_is_refused(tmp_path, inputs):
    result = dry(tmp_path, inputs, "--cache", "relative/cache")
    assert result.returncode == 2 and "--cache" in result.stderr
