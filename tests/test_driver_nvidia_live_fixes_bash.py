"""The bash twin of tests/test_driver_nvidia_live_fixes.py: the NVIDIA driver update bugs found live on v0.38.1 (VM 984)."""
from __future__ import annotations

import json

from osx_proxmox_next import driver_engine as engine
from osx_proxmox_next.driver_plan import DriverPlan
from osx_proxmox_next.driver_targets import Target
from test_driver_parity import (  # noqa: F401 - fixtures and helpers
    NV, NV_STUBS, SNAP, STUB_NULLMOTH, UNDO, host, needs_bash4, write_record)


@needs_bash4
def test_a_failed_read_of_the_opencore_disk_never_leaks_a_traceback(host):
    """Live: the bash NVIDIA update printed a JSONDecodeError traceback after "Staged ...": the read of AMFIPass gave no JSON."""
    stub = 'driver_oc_kext() { echo "mount failed"; return 1; }\n'
    res = host.run(stub + "driver_oc_json 109 versions AMFIPass.kext; echo \"rc=$?\"\n")
    assert res.stdout == "{}\nrc=1\n" and "Traceback" not in res.stderr
    res = host.run('driver_oc_kext() { echo "not json at all"; }\nDRIVER_AMFI_ZIP=/z NULLMOTH_AMFIPASS_URL=https://x/AMFIPass-v1.4.1-RELEASE.zip '
                   'NULLMOTH_AMFIPASS_SHA256=' + "7" * 64 + '\ndriver_refresh_amfipass 109\n')
    assert "Traceback" not in res.stderr and "JSONDecodeError" not in res.stderr + res.stdout
    ok = host.run('driver_oc_kext() { echo "NOTE: x"; echo \'{"AMFIPass.kext": "1.4.1"}\'; }\ndriver_oc_json 109 versions AMFIPass.kext\n')
    assert json.loads(ok.stdout) == {"AMFIPass.kext": "1.4.1"}


@needs_bash4
def test_the_bash_nvidia_text_says_the_nullmoth_driver_not_the_graphics_package(host):
    """Live: "Staging the graphics package inside macOS" and "Staged the graphics package" in an NVIDIA update."""
    out = host.run('IGPU_STAGE_WHAT="the NullMoth driver"\ntype igpu_stage | grep -c \'what}\'\n').stdout
    assert int(out.strip()) >= 2
    from test_driver_parity import BASH
    stage = BASH[BASH.index("function nullmoth_stage()"):][:200]
    assert 'local IGPU_STAGE_WHAT="the NullMoth driver"' in stage


@needs_bash4
def test_the_bash_plan_of_a_mac_that_is_up_to_date_says_kept_not_reinstalled(host):
    """Live: `driver-update --version v2.8.1` on a Mac at v2.8.1 printed "v2.8.1, reinstalled" then "Up to date"."""
    write_record(host, "nvidia", NullMoth="v2.9")
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run\n").stdout.splitlines()
    plan = DriverPlan(109, "macos-sequoia", "nvidia", "sequoia", False, {"NullMoth": "v2.9"}, True, Target("nvidia", NV), "current",
                      snapshot=SNAP, undo=UNDO)
    assert out == plan.lines() and out[1] == "  NullMoth: already v2.9, kept" and out[-1] == engine.UP_TO_DATE
    out = host.run(STUB_NULLMOTH + "driver_update_command 109 --dry-run --reinstall\n").stdout
    assert "NullMoth: v2.9, reinstalled" in out and "Steps:" in out


@needs_bash4
def test_the_bash_run_lists_the_steps_its_plan_listed(host):
    """Live: the plan of a stopped Mac listed 6 steps and the run 7."""
    for status, first in (("stopped", "snapshot"), ("running", "stop")):
        write_record(host, "nvidia", NullMoth="v2.8.1")
        (host.qm_dir / "status").write_text(status + "\n")
        snippet = (NV_STUBS + "nullmoth_stage() { return 1; }\nDRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\ncfg=$(qm config 109)\n"
                   f"driver_apply 109 nvidia \"$cfg\" sequoia {SNAP} yes v2.9 no macos-sequoia\n")
        host.run(snippet, expect=1)
        steps = [r["id"] for r in engine.read_update(109, str(host.state))["steps"]]
        assert steps[0] == first and len(steps) == (6 if status == "stopped" else 7)


@needs_bash4
def test_bash_reads_the_version_of_a_rolled_back_mac_from_its_opencore_disk(host):
    """Live: after a manual qm rollback the status said "installed version unknown" instead of v2.8.1."""
    (host.qm_dir / "uuids").write_text("/dev/pve/vm-9-disk-1 AAA\n")
    fp = host.run("driver_fingerprint 109\n").stdout.strip()
    write_record(host, "nvidia", NullMoth="v3.0")
    rec = json.loads((host.state / "109.json").read_text())
    rec["fingerprint"] = fp
    (host.state / "109.json").write_text(json.dumps(rec))
    (host.qm_dir / "uuids").write_text("/dev/pve/vm-9-disk-1 ZZZ\n")                  # the rollback made a new volume
    marker = 'driver_oc_kext() { echo "oc_kext $*" >> "$QM_DIR/oc.log"; echo \'{"NullMoth": "v2.8.1"}\'; }\n'
    assert json.loads(host.run(marker + "driver_installed 109 nvidia no\n").stdout) == {"NullMoth": "v2.8.1"}
    assert (host.qm_dir / "oc.log").read_text().strip() == "oc_kext 109 marker NullMoth"
    status = host.run(marker + STUB_NULLMOTH + "driver_status_command 109\n").stdout
    assert "Installed: NullMoth v2.8.1" in status
    nothing = 'driver_oc_kext() { echo \'{"NullMoth": ""}\'; }\n'
    status = host.run(nothing + STUB_NULLMOTH + "driver_status_command 109\n").stdout
    assert f"Installed: installed version unknown ({engine.STALE_RECORD})" in status
