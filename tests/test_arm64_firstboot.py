"""snapintosh firstboot: the dry run writes nothing, the inputs are validated, and the helper logic is exercised without QEMU."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "arm64" / "snapintosh"
HELPERS = ROOT / "arm64" / "lib" / "install" / "firstboot"
ECID = "4533301000242790675"


def load(name):
    spec = importlib.util.spec_from_file_location(name, HELPERS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(tmp_path, args, extra_env=None):
    env = dict(os.environ, SNAPINTOSH_HOME=str(tmp_path / "home"), XDG_CONFIG_HOME=str(tmp_path / "cfg"),
               HOME=str(tmp_path))
    env.update(extra_env or {})
    return subprocess.run(["bash", str(CLI), *args], cwd=tmp_path, env=env, capture_output=True, text=True)


def inputs(tmp_path):
    disk = tmp_path / "macos.raw"
    disk.write_bytes(b"disk")
    aux = tmp_path / "aux.img"
    aux.write_bytes(b"aux")
    kdk = tmp_path / "kdk"
    kdk.mkdir()
    template = tmp_path / "golden-policy.img4"
    template.write_bytes(b"tpl")
    return disk, aux, kdk, template


def args(tmp_path, disk, aux, kdk, template, *extra):
    return ["firstboot", "--disk", str(disk), "--aux", str(aux), "--ecid", ECID, "--kdk", str(kdk),
            "--template", str(template), *extra]


def test_the_dry_run_prints_the_plan_and_writes_nothing(tmp_path):
    disk, aux, kdk, template = inputs(tmp_path)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    result = run(tmp_path, args(tmp_path, disk, aux, kdk, template, "--dry-run"))
    assert result.returncode == 0, result.stderr
    assert "firstboot plan (dry run, nothing is written)" in result.stdout
    assert "single-user offline pass" in result.stdout and "VNC tunnel" in result.stdout
    assert "MODE=desktop" in result.stdout and "local-policy.img4" in result.stdout
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert after == before and not (tmp_path / "home").exists()


def test_the_default_user_and_password_are_named(tmp_path):
    disk, aux, kdk, template = inputs(tmp_path)
    result = run(tmp_path, args(tmp_path, disk, aux, kdk, template, "--dry-run"))
    assert "user:     snap" in result.stdout and "default snaplab" in result.stdout


def test_a_bad_user_is_refused_before_anything_starts(tmp_path):
    disk, aux, kdk, template = inputs(tmp_path)
    result = run(tmp_path, args(tmp_path, disk, aux, kdk, template, "--user", "9snap", "--dry-run"))
    assert result.returncode == 2 and "--user" in result.stderr


def test_a_bad_ecid_is_refused(tmp_path):
    disk, aux, kdk, template = inputs(tmp_path)
    result = run(tmp_path, args(tmp_path, disk, aux, kdk, template, "--ecid", "abc", "--dry-run"))
    assert result.returncode == 2 and "ecid" in result.stderr


def test_missing_inputs_are_named(tmp_path):
    disk, aux, kdk, template = inputs(tmp_path)
    result = run(tmp_path, ["firstboot", "--disk", str(disk), "--aux", str(aux), "--ecid", ECID,
                            "--kdk", str(kdk), "--template", str(tmp_path / "nope.img4"), "--dry-run"])
    assert result.returncode == 2 and "no such policy template" in result.stderr


def test_a_password_file_that_is_not_there_is_named(tmp_path):
    disk, aux, kdk, template = inputs(tmp_path)
    result = run(tmp_path, args(tmp_path, disk, aux, kdk, template, "--password-file", str(tmp_path / "no"), "--dry-run"))
    assert result.returncode == 2 and "no such password file" in result.stderr


def test_an_unknown_option_is_named(tmp_path):
    disk, aux, kdk, template = inputs(tmp_path)
    result = run(tmp_path, args(tmp_path, disk, aux, kdk, template, "--bogus", "--dry-run"))
    assert result.returncode == 2 and "--bogus" in result.stderr


def test_the_aux_nonce_values_are_hashes_of_bank_zero(tmp_path):
    import hashlib
    read_values = load("read-values")
    aux = tmp_path / "aux.img"
    slot0 = bytes(range(16))
    slot1 = bytes(range(16, 32))
    aux.write_bytes(b"prefix boop-storage-nonces=" + slot0 + slot1 + b"\0" * 80 + b"suffix")
    rpnh, lpnh = read_values.aux_values(str(aux))
    assert rpnh == hashlib.sha384(slot0).hexdigest()
    assert lpnh == hashlib.sha384(slot1).hexdigest()


def test_the_restore_qemu_is_stopped_before_firstboot_opens_the_disk():
    """The restore QEMU flushes the keybag into the disk on exit; firstboot uses the same file with locking off."""
    script = (ROOT / "arm64" / "lib" / "install" / "go.sh").read_text()
    body = script.split("stop_qemu()", 1)[1]
    assert "pkill -x qemu-system-aar" in body and "sync" in body
    assert script.count("stop_qemu\n") >= 2


def test_the_desktop_frame_is_taken_again_before_it_is_called_black():
    script = (HELPERS / "run.sh").read_text()
    assert "for _ in 1 2 3 4 5; do" in script
    assert "sleep 5" in script and 'fail "the captured frame is black"' in script


def test_the_policy_nonces_and_the_pflash_come_from_one_aux():
    """BootPolicy panics when the NVRAM nonces and the LocalPolicy disagree, so both passes read the same aux."""
    script = (HELPERS / "run.sh").read_text()
    assert '[ -s "$LIVE_AUX" ] && AUX_EFF=$LIVE_AUX' in script
    assert script.count('values "$DISK" "$AUX_EFF"') == 2
    assert 'dd if="$AUX_EFF" of="$WORK/single/aux"' in script
    assert 'cp "$WORK/single/aux" "$WORK/desktop/aux"' in script


def test_the_aux_needs_the_marker(tmp_path):
    read_values = load("read-values")
    aux = tmp_path / "aux.img"
    aux.write_bytes(b"no marker")
    try:
        read_values.aux_values(str(aux))
    except SystemExit as exc:
        assert "boop-storage-nonces" in str(exc)
    else:
        raise AssertionError("aux_values accepted an aux with no marker")


def test_a_non_black_frame_passes_and_a_black_one_fails(tmp_path):
    frame = load("frame")
    good = tmp_path / "good.ppm"
    width, height = 2, 2
    pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    good.write_bytes(b"P6\n%d %d\n255\n" % (width, height) + pixels)
    assert frame.stats(str(good))[2] > 2.0 and frame.stats(str(good))[3] >= 3
    black = tmp_path / "black.ppm"
    black.write_bytes(b"P6\n2 2\n255\n" + b"\0" * 12)
    assert frame.stats(str(black))[2] == 0.0


def test_login_keys_cover_the_supported_alphabet_and_refuse_the_rest():
    login = load("qmp-login")
    assert login.key_for("s") == [{"type": "qcode", "data": "s"}]
    assert login.key_for("A") == [{"type": "qcode", "data": "shift"}, {"type": "qcode", "data": "a"}]
    try:
        login.key_for("é")
    except ValueError as exc:
        assert "not supported" in str(exc)
    else:
        raise AssertionError("qmp-login accepted an unsupported character")
