"""Fix wave 3: no code path of the tool creates a device map before it knows it may read, and every loop device and mount
a path makes is released on every way out.

The scripts the tool generates for its OpenCore and payload disks run for real in bash, with fake host commands that
keep a ledger: a loop device attached and not detached, a mount not unmounted, and a `partprobe` that is not given a
loop device (a bare one re-reads every disk of the host and makes partition maps on the logical volumes of running
Macs, which then block the next rollback)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from osx_proxmox_next import driver_oc, igpu_engine, oc_builder
from osx_proxmox_next.planner import mount_oc_disk_script, stopped_guard
from osx_proxmox_next.script_renderer import _partprobe_retry_snippet

FAKES = {
    "qm": '''#!/bin/bash
case "$1" in
  status) echo "status: $(cat "$FAKE/status")" ;;
  config) echo "ide0: local-lvm:vm-9-disk-1,media=disk,size=1G"; echo "virtio0: local-lvm:vm-9-disk-2,size=64G" ;;
esac
''',
    "pvesm": '#!/bin/bash\n[ "$1" = path ] && echo "/dev/pve/${2#*:}"\nexit 0\n',
    "lvs": '#!/bin/bash\n[ -f "$FAKE/no_lv" ] && exit 5\nexit 0\n',
    "lvchange": '#!/bin/bash\n[ -f "$FAKE/lvchange_fails" ] && exit 5\nexit 0\n',
    "losetup": '''#!/bin/bash
case "$1" in
  -fP) [ -f "$FAKE/losetup_fails" ] && exit 1
       echo "/dev/loop7"; echo "/dev/loop7" >> "$FAKE/loops" ;;
  -d)  grep -vx "$2" "$FAKE/loops" > "$FAKE/loops.new"; mv "$FAKE/loops.new" "$FAKE/loops" ;;
esac
exit 0
''',
    # a device that is not a loop device (or none) gets partition maps: that is the leak
    "partprobe": '''#!/bin/bash
case "$1" in
  /dev/loop*) echo "ok $1" >> "$FAKE/ledger" ;;
  *) echo "MAP-LEAK partprobe '$*'" >> "$FAKE/ledger" ;;
esac
exit 0
''',
    "blockdev": '#!/bin/bash\nexit 0\n',
    "mount": '''#!/bin/bash
[ -f "$FAKE/mount_fails" ] && exit 32
for last; do :; done
echo "$last" >> "$FAKE/mounts"
mkdir -p "$last/EFI/OC/Kexts"
''',
    "umount": '''#!/bin/bash
for last; do :; done
grep -vx "$last" "$FAKE/mounts" > "$FAKE/mounts.new"; mv "$FAKE/mounts.new" "$FAKE/mounts"
''',
    "sync": "#!/bin/bash\nexit 0\n",
    "sleep": "#!/bin/bash\nexit 0\n",
    "mkfs.fat": "#!/bin/bash\nexit 0\n",
    "sgdisk": "#!/bin/bash\nexit 0\n",
    "truncate": "#!/bin/bash\nexit 0\n",
}
# a loop partition cannot be a real block device in a test: `[ -b /dev/loopNp1 ]` answers yes unless the test says no
BASH_ENV = '''[() { if [[ "$1" == -b && "$2" == /dev/loop* ]]; then [ ! -f "$FAKE/no_part" ]; return; fi; builtin [ "$@"; }
'''


@pytest.fixture()
def host(tmp_path):
    fake = tmp_path / "fake"
    fake.mkdir()
    for name, body in FAKES.items():
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    (fake / "status").write_text("stopped")
    (fake / "loops").write_text("")
    (fake / "mounts").write_text("")
    (fake / "ledger").write_text("")
    (fake / "env.sh").write_text(BASH_ENV)
    return fake


def walk(host: Path, script: str) -> tuple[int, str]:
    """Run *script* with the fake host first on PATH. Returns (exit code, output)."""
    env = dict(os.environ, PATH=f"{host}:{os.environ['PATH']}", FAKE=str(host), BASH_ENV=str(host / "env.sh"))
    empty = host.parent / "cwd"      # the retry loop globs `p*`: a folder with a file of that name hides the leak
    empty.mkdir(exist_ok=True)
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60, cwd=empty)
    return done.returncode, done.stdout + done.stderr


def ledger(host: Path) -> list[str]:
    return [l for l in (host / "ledger").read_text().splitlines() if l]


def open_things(host: Path) -> dict[str, list[str]]:
    return {"loops": [l for l in (host / "loops").read_text().split() if l],
            "mounts": [l for l in (host / "mounts").read_text().split() if l]}


def assert_clean(host: Path) -> None:
    assert not [l for l in ledger(host) if l.startswith("MAP-LEAK")], ledger(host)
    assert open_things(host) == {"loops": [], "mounts": []}


SCRIPTS = {
    "read": lambda: driver_oc.kext_script("9", [["versions", "WhateverXe.kext"]]),
    "replace": lambda: driver_oc.kext_script("9", [["replace", "Lilu.kext", "/nonexistent.zip", "0" * 64]]),
    "mount-rw": lambda: mount_oc_disk_script("9") + "true",
    "mount-ro-stopped-only": lambda: mount_oc_disk_script("9", ro=True, require_stopped=True) + "true",
}
# what makes a path stop early, and what the path must still have done (or not done) by then
FAILURES = ["status-running", "no-lv", "lvchange_fails", "losetup_fails", "no_part", "mount_fails", None]


@pytest.mark.parametrize("name", sorted(SCRIPTS))
@pytest.mark.parametrize("failure", FAILURES)
def test_every_opencore_access_path_leaves_no_map_loop_or_mount_whatever_stops_it(host, name, failure):
    if failure == "status-running":
        (host / "status").write_text("running")
    elif failure:
        (host / failure).write_text("1")
    walk(host, SCRIPTS[name]())
    assert_clean(host)


def test_a_refused_read_of_a_running_mac_touches_no_device_at_all(host):
    (host / "status").write_text("running")
    code, out = walk(host, SCRIPTS["read"]())
    assert code != 0 and "not stopped" in out
    assert ledger(host) == [] and open_things(host) == {"loops": [], "mounts": []}


def test_the_partprobe_retry_does_nothing_without_a_loop_device(host):
    code, _out = walk(host, "LOOP=''; " + _partprobe_retry_snippet("LOOP") + " && echo reached")
    assert code != 0 and ledger(host) == []


def test_the_partprobe_retry_works_on_a_loop_device_and_ends_true(host):
    code, out = walk(host, "LOOP=/dev/loop7; " + _partprobe_retry_snippet("LOOP") + " && echo reached")
    assert code == 0 and "reached" in out and ledger(host)[0] == "ok /dev/loop7"


def test_the_partprobe_retry_does_not_break_out_of_an_and_chain_that_already_failed(host):
    code, out = walk(host, "LOOP=''; false && " + _partprobe_retry_snippet("LOOP") + " && echo reached")
    assert code != 0 and "reached" not in out and ledger(host) == []


@pytest.mark.parametrize("failure", ["losetup_fails", "no_part", None])
def test_the_opencore_image_builder_never_runs_a_bare_partprobe(host, tmp_path, failure):
    if failure:
        (host / failure).write_text("1")
    script = oc_builder._iso_build_script(tmp_path / "tree", tmp_path / "oc.img")
    walk(host, "mkdir -p " + str(tmp_path / "tree") + "; " + script)
    assert not [l for l in ledger(host) if l.startswith("MAP-LEAK")]


def test_a_refused_guard_does_not_run_what_follows(host):
    (host / "status").write_text("running")
    code, out = walk(host, stopped_guard("9") + "echo ran")
    assert code != 0 and "ran" not in out.replace("not stopped", "")


class LoopRun:
    """Stands in for the payload code's command runner and keeps the same ledger of loop devices."""

    def __init__(self, fail_part=False, attach_fails=False):
        self.loops, self.calls, self.fail_part, self.attach_fails = [], [], fail_part, attach_fails

    def __call__(self, argv, timeout=0):
        self.calls.append(argv)
        if argv[0] == "losetup" and argv[1] == "-fP":
            if self.attach_fails:
                return 1, "no loop"
            self.loops.append("/dev/loop9")
            return 0, "/dev/loop9\n"
        if argv[:2] == ["losetup", "-d"]:
            self.loops.remove(argv[2])
        if argv[0] == "partprobe":
            assert argv[1].startswith("/dev/loop"), argv
        if argv[0] == "test":
            return (1 if self.fail_part else 0), ""
        return 0, ""


@pytest.mark.parametrize("kwargs", [{}, {"fail_part": True}, {"attach_fails": True}])
def test_the_payload_image_access_releases_its_loop_device_and_never_probes_a_bare_device(tmp_path, kwargs):
    img = tmp_path / "payload.img"
    img.write_bytes(b"\0" * 1024)
    run = LoopRun(**kwargs)
    igpu_engine.read_result(str(img), run=run)
    assert run.loops == []


def test_the_python_side_releases_the_macs_maps_after_a_refused_read(monkeypatch):
    """Even a script that refuses leaves no partition map of this tool on the Mac's volumes: the maps are released."""
    from test_driver_disks import MAP2, MAP3, MapPve

    pve = MapPve(status="running")
    pve.bash_ok, pve.bash_output = False, "ERROR: the Mac is not stopped, so its OpenCore disk is not touched."
    with pytest.raises(driver_oc.OcError):
        driver_oc.read_versions(pve, 9, ["Lilu"])
    assert pve.maps == [] and sorted(pve.removed) == sorted([MAP2, MAP3])


def test_the_python_side_releases_the_maps_after_a_replace_too():
    from test_driver_disks import MapPve

    pve = MapPve(status="stopped")
    pve.bash_output = "VERSION 1.7.2"
    driver_oc.replace_kexts(pve, 9, [("Lilu", Path("/tmp/x.zip"), "0" * 64)])
    assert pve.maps == []
