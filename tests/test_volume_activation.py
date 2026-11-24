"""A stopped VM's disk is activated before the host reads or writes it (PVE 9 leaves guest LVs inactive after a
boot; a rolled-back thin volume keeps the activation-skip flag): the Python adapter, the generated scripts and the
bash installer all do it, for lvm and lvmthin only."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from osx_proxmox_next import planner
from osx_proxmox_next.infrastructure import CommandResult, ProxmoxAdapter
from test_gpu_parity import BASH, BLOCK, STUBS

LV = "/dev/pve/vm-105-disk-2"


class Adapter(ProxmoxAdapter):
    def __init__(self, path: str, lvs_ok: bool = True) -> None:
        self.path, self.lvs_ok, self.calls = path, lvs_ok, []

    def run(self, argv):
        self.calls.append(argv)
        if argv[:2] == ["pvesm", "path"]:
            return CommandResult(True, 0, self.path + "\n")
        return CommandResult(argv[0] != "lvs" or self.lvs_ok, 0 if argv[0] != "lvs" or self.lvs_ok else 5, "")


def test_the_adapter_activates_a_logical_volume_before_it_hands_back_the_path():
    pve = Adapter(LV)
    assert pve.volume_path("local-lvm:vm-105-disk-2") == LV
    assert ["lvchange", "-ay", "-K", LV] in pve.calls


@pytest.mark.parametrize("path,lvs_ok", [("/dev/zvol/rpool/data/vm-1-disk-0", True), ("/dev/rbd/pool/img", True),
                                         ("/mnt/pve/nfs/images/105/vm-105-disk-0.raw", True), ("/dev/sdb", False)])
def test_the_adapter_leaves_every_other_storage_alone(path, lvs_ok):
    pve = Adapter(path, lvs_ok)
    assert pve.volume_path("s:v") == path
    assert not [c for c in pve.calls if c[0] == "lvchange"]


def stubs(tmp_path: Path, lvs: int = 0, lvchange: int = 0, path: str = LV) -> dict:
    bin_dir = tmp_path / "stubs"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "calls.log"
    for name, code in (("lvs", lvs), ("lvchange", lvchange)):
        (bin_dir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit {code}\n')
        (bin_dir / name).chmod(0o755)
    (bin_dir / "pvesm").write_text(f"#!/bin/sh\n[ \"$1\" = path ] && echo {path}\n")
    (bin_dir / "pvesm").chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "LOG": str(log)}


def run_bash(code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, env=env, timeout=30)


def log_of(env: dict) -> str:
    path = Path(env["LOG"])
    return path.read_text() if path.exists() else ""


def test_the_generated_snippet_activates_an_lv_and_fails_with_the_real_error(tmp_path):
    env = stubs(tmp_path)
    res = run_bash(f'P={LV}; ' + planner.activate_volume_snippet("P") + "echo go", env)
    assert res.stdout.strip() == "go" and f"lvchange -ay -K {LV}" in log_of(env)
    env = stubs(tmp_path / "bad", lvchange=1)
    res = run_bash(f'P={LV}; ' + planner.activate_volume_snippet("P") + "echo go", env)
    assert res.returncode != 0 and "ERROR: could not activate the disk" in res.stdout and "go" not in res.stdout
    env = stubs(tmp_path / "zfs")
    res = run_bash('P=/dev/zvol/rpool/x; ' + planner.activate_volume_snippet("P") + "echo go", env)
    assert res.stdout.strip() == "go" and "lvchange" not in log_of(env)


def test_the_opencore_mount_script_activates_the_disk_before_the_loop_device():
    script = planner.mount_oc_disk_script("105")
    assert script.index("lvchange -ay -K") < script.index("losetup -fP")


def test_the_bash_helper_does_the_same_and_every_path_lookup_uses_it(tmp_path):
    func = BLOCK[BLOCK.index("function pve_vol_path()"):BLOCK.index("GPU_ISSUES=()")]
    env = stubs(tmp_path)
    res = run_bash(STUBS + func + '\npve_vol_path "local-lvm:vm-105-disk-2"', env)
    assert res.stdout.strip() == LV and f"lvchange -ay -K {LV}" in log_of(env)
    env = stubs(tmp_path / "zfs", path="/dev/zvol/rpool/x")
    assert run_bash(STUBS + func + '\npve_vol_path "z:v"', env).stdout.strip() == "/dev/zvol/rpool/x"
    assert "lvchange" not in log_of(env)
    env = stubs(tmp_path / "bad", lvchange=1)
    res = run_bash(STUBS + func + '\npve_vol_path "z:v"', env)
    assert res.returncode != 0 and "could not activate" in res.stderr
    assert 'pvesm path "$vol"' not in BASH and 'pvesm path "$ref"' not in BASH and 'pvesm path "$payload_ref"' not in BASH
