"""The bash installer unpacks its embedded engines (about 350 KB) once per run, into one `gpuengine.XXXXXX` directory, and removes it when
the process ends. It used to unpack a copy on every call that ran in a subshell, because the variable that remembered the directory
was lost with the subshell, and none was ever removed. These tests run the whole script on the stubbed host, as root, with TMPDIR in
the test's own directory."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

import temp_size
from test_driver_parity import REPO, host, needs_bash4  # noqa: F401 - fixtures and helpers
from test_driver_status_record_parity import FAKE_NET, canned_releases, record

SCRIPT = REPO / "scripts/bash/osx-proxmox-next.sh"
# The hook runs once the script has set itself up and before it looks at its arguments (the post-install line exists in every version).
MARKER = 'if [ "${1:-}" == "post-install" ]; then'
BLOCK_START, BLOCK_END = "# ── Vendor-neutral GPU helpers begin ──", "# ── GPU walkthrough end ──"


def engine_dirs(root):
    """Every gpuengine.* directory below root, at any depth (the script keeps its own temp dir inside TMPDIR)."""
    return temp_size.engine_copies(root)


def prepare(host, tmp_path, net=False):
    """The script under test with an OSX_TEST_HOOK eval, the id stub that makes it root, and a qm that counts the engine dirs it sees."""
    script = tmp_path / "script-under-test.sh"
    text = SCRIPT.read_text()
    assert text.count(MARKER) == 1
    script.write_text(text.replace(MARKER, 'eval "${OSX_TEST_HOOK:-:}"\n' + MARKER))
    bin_dir = host.dir / "bin"
    (bin_dir / "id").write_text('#!/bin/sh\n[ "$1" = -u ] && echo 0 || exec /usr/bin/id "$@"\n')
    (bin_dir / "id").chmod(0o755)
    (bin_dir / "qm.real").write_text((bin_dir / "qm").read_text())
    (bin_dir / "qm.real").chmod(0o755)
    (bin_dir / "qm").write_text('#!/bin/bash\nfind "$TMPDIR" -type d -name "gpuengine.*" -not -path "*/Library/*" 2>/dev/null | wc -l >> "$COUNT_LOG"\n'
                                'exec "$(dirname "$0")/qm.real" "$@"\n')
    (bin_dir / "qm").chmod(0o755)
    # GNU mktemp (Linux) makes a bare `mktemp -d` in $TMPDIR; the BSD one on a Mac does not. This is the GNU behaviour on both.
    (bin_dir / "mktemp").write_text('#!/bin/sh\n[ "$#" -le 1 ] && [ "${1:--d}" = -d ] && exec /usr/bin/mktemp ${1:-} "${TMPDIR:-/tmp}/tmp.XXXXXXXXXX"\n'
                                    'exec /usr/bin/mktemp "$@"\n')
    (bin_dir / "mktemp").chmod(0o755)
    host.env.update(TERM="xterm", COUNT_LOG=str(tmp_path / "counts.log"))
    if net:
        fake = tmp_path / "fakenet"
        fake.mkdir()
        (fake / "sitecustomize.py").write_text(FAKE_NET)
        (fake / "releases.json").write_text(json.dumps(canned_releases()))
        host.env.update(PYTHONPATH=f"{fake}:{host.env['PYTHONPATH']}", FAKE_RELEASES=str(fake / "releases.json"),
                        FAKE_DOWNLOADS=str(tmp_path / "downloads.log"))
    return script


def run(host, script, *args, hook="", env=None):
    full = dict(host.env, OSX_TEST_HOOK=hook, **(env or {}))
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=full, timeout=120)


def counts(tmp_path):
    log = tmp_path / "counts.log"
    return [int(n) for n in log.read_text().split()] if log.exists() else []


def running_host(host, tmp_path):
    record(host, "intel", WhateverXe="0.8.0", WhateverXe_Graphics="0.4.0")
    host.config()
    (host.qm_dir / "status").write_text("running\n")
    return prepare(host, tmp_path, net=True)


@needs_bash4
@pytest.mark.parametrize("args", [("driver-status", "109", "--json"), ("driver-status", "109")])
def test_a_status_leaves_no_engine_directory_and_never_has_more_than_one(host, tmp_path, args):
    script = running_host(host, tmp_path)
    res = run(host, script, *args)
    assert res.returncode == 0, res.stderr
    assert engine_dirs(tmp_path) == []
    assert counts(tmp_path) and max(counts(tmp_path)) == 1, counts(tmp_path)         # many engine calls, one directory


@needs_bash4
def test_every_subshell_of_one_run_uses_the_same_directory(host, tmp_path):
    script = prepare(host, tmp_path)
    hook = ('a=$(gpu_engine_init; echo "$GPU_ENGINE_DIR"); b=$(gpu_engine_init; echo "$GPU_ENGINE_DIR"); echo x | { gpu_engine_init; '
            'echo "$GPU_ENGINE_DIR" > "$SEEN"; }; echo "$a $b $GPU_ENGINE_DIR" >> "$SEEN"; exit 0')
    res = run(host, script, hook=hook, env={"SEEN": str(tmp_path / "seen")})
    assert res.returncode == 0, res.stderr
    lines = (tmp_path / "seen").read_text().splitlines()
    assert len(set(lines[0].split()) | set(lines[1].split())) == 1 and engine_dirs(tmp_path) == []


@needs_bash4
@pytest.mark.parametrize("args, hook, code", [
    (("driver-status", "109", "--json"), "", 2),                    # a refusal: no iGPU on this VM
    (("driver-status", "--json"), "", 1),                           # a usage error
    (("driver-status", "109"), "false", 1),                         # an error under set -e (the ERR trap)
])
def test_the_directory_is_removed_when_the_command_fails(host, tmp_path, args, hook, code):
    host.config(hostpci0=None)
    script = prepare(host, tmp_path)
    res = run(host, script, *args, hook=hook)
    assert res.returncode == code, (res.stdout, res.stderr)
    assert engine_dirs(tmp_path) == []


HANG = ('x=$(driver_engine pending-kind 109 2>/dev/null) || true; y=$(driver_engine pending-kind 109 2>/dev/null) || true; '
        'touch "$READY"; ')
WAIT = {signal.SIGINT: "sleep 60 >/dev/null 2>&1",                 # Ctrl-C ends a foreground command with the script
        signal.SIGTERM: "sleep 60 >/dev/null 2>&1 & wait $!"}      # kill <pid> reaches the script alone: it waits in a builtin


@needs_bash4
@pytest.mark.parametrize("sig, status", [(signal.SIGINT, -signal.SIGINT), (signal.SIGTERM, -signal.SIGTERM)])
def test_the_directory_is_removed_when_the_script_is_signalled(host, tmp_path, sig, status):
    script = prepare(host, tmp_path)
    ready = tmp_path / "ready"
    env = dict(host.env, OSX_TEST_HOOK=HANG + WAIT[sig], READY=str(ready))
    proc = subprocess.Popen(["bash", str(script), "driver-status", "109"], env=env, start_new_session=True,
                            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),   # a job started with & ignores SIGINT
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 60
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists() and engine_dirs(tmp_path)           # the engines are in use when the signal comes
        if sig == signal.SIGINT:
            os.killpg(proc.pid, sig)                              # Ctrl-C reaches the whole foreground group
        else:
            os.kill(proc.pid, sig)
        assert proc.wait(timeout=30) == status                    # it still ends the way the signal ends it
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)                   # the sleep the hook started
        except ProcessLookupError:
            pass
    assert engine_dirs(tmp_path) == []


@needs_bash4
def test_the_existing_traps_still_run_on_an_error(host, tmp_path):
    host.config()
    script = prepare(host, tmp_path)
    res = run(host, script, "driver-status", "109", hook="VMID=777; false")
    assert res.returncode == 1 and "[ERROR]" in res.stdout
    calls = [c for c in host.calls() if " 777" in c]
    assert calls == ["status 777", "stop 777", "destroy 777 --purge"]        # cleanup_vmid ran, in order
    assert engine_dirs(tmp_path) == []


@needs_bash4
def test_a_subshell_never_removes_the_directory_but_the_process_does_at_its_end(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    snippet = ('( gpu_engine_remove ); echo "$(gpu_engine_remove)" | { gpu_engine_remove; }; [ -f "$GPU_ENGINE_DIR/host_setup.py" ] || exit 7\n'
               'for i in 1 2 3; do a=$(gpu_engine_init; echo "$GPU_ENGINE_DIR"); done; [ "$a" = "$GPU_ENGINE_DIR" ] || exit 8\n'
               'echo "$GPU_ENGINE_DIR" > ' + str(tmp_path / "dir"))
    proc = sourced(tmp_path, root, snippet)
    assert proc.wait(timeout=60) == 0, proc.stderr.read()
    assert (tmp_path / "dir").read_text().strip().startswith(str(root) + "/gpuengine.") and list(root.iterdir()) == []


def sourced(tmp_path, root, snippet, **kw):
    """Part of the script with the engines in it, sourced the way the other bash tests do, with `root` as its temp dir."""
    text = SCRIPT.read_text()
    block = tmp_path / "block.sh"
    block.write_text(text[text.index(BLOCK_START):text.index(BLOCK_END)])
    env = dict(os.environ, TEMP_DIR=str(root), TMPDIR=str(root))
    return subprocess.Popen(["bash", "-c", f"set -u; source {block}\ngpu_engine_setup; trap gpu_engine_remove EXIT\n{snippet}"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


@needs_bash4
def test_a_directory_that_is_not_this_runs_stays_untouched(host, tmp_path):
    script = prepare(host, tmp_path)
    host.config(hostpci0=None)
    theirs = tmp_path / "gpuengine.theirs1"
    theirs.mkdir()
    (theirs / "marker").write_text("another instance")
    assert run(host, script, "driver-status", "109", "--json").returncode == 2
    assert (theirs / "marker").read_text() == "another instance"
    # two instances in one temp dir: the one that ends takes away its own directory and nothing else
    root = tmp_path / "shared"
    root.mkdir()
    sibling = root / "gpuengine.sibling"
    sibling.mkdir()
    (sibling / "marker").write_text("sibling")
    first = sourced(tmp_path, root, f'echo "$GPU_ENGINE_DIR" > {tmp_path}/up.tmp; mv {tmp_path}/up.tmp {tmp_path}/up; sleep 30 & wait $!')
    try:
        for _ in range(600):
            if (tmp_path / "up").exists():
                break
            time.sleep(0.05)
        second = sourced(tmp_path, root, 'gpu_engine_init; ls "$GPU_ENGINE_DIR" >/dev/null')
        assert second.wait(timeout=60) == 0
        left = {p.name for p in root.iterdir()}
        assert len(left) == 2 and "gpuengine.sibling" in left and (sibling / "marker").read_text() == "sibling"
        mine = (tmp_path / "up").read_text().strip()
        assert mine.startswith(str(root) + "/gpuengine.") and (Path(mine) / "host_setup.py").is_file()   # the first instance's engines are still there
    finally:
        first.kill()
        first.wait()
        shutil.rmtree(theirs)                                       # made up here: the fixture in conftest fails a test that leaves any
        shutil.rmtree(root)


@needs_bash4
@pytest.mark.parametrize("which", ["data", "root"])
def test_a_directory_from_the_environment_is_ignored_and_nothing_in_it_is_deleted(host, tmp_path, which):
    script = running_host(host, tmp_path)
    victim = tmp_path / "data"
    victim.mkdir()
    (victim / "keep.txt").write_text("mine")
    target = str(victim) if which == "data" else "/"
    res = run(host, script, "driver-status", "109", "--json",
              env={"GPU_ENGINE_DIR": target, "GPU_ENGINE_ROOT": str(tmp_path) if which == "data" else "", "GPU_ENGINE_OWNER": "1"})
    assert res.returncode == 0 and json.loads(res.stdout)["vmid"] == 109, (res.stdout, res.stderr)
    assert [p.name for p in victim.iterdir()] == ["keep.txt"] and (victim / "keep.txt").read_text() == "mine"
    assert engine_dirs(tmp_path) == [] and max(counts(tmp_path)) == 1
