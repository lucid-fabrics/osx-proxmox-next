"""The arm64 (beta) runner plan: qm and shell steps, cloud-init files, and nothing spliced into a shell."""
from __future__ import annotations

import pytest

from arm64_fakes import REF, RENDERER_SHA, SHA
from osx_proxmox_next.arm64 import ARM64_TAG, DEBIAN_IMAGE_URL, DEBIAN_SUMS_URL, Arm64Config
from osx_proxmox_next.arm64_cloudinit import (
    INSTALL_DIR,
    LOG_PATH,
    STATUS_PATH,
    render_cloud_init,
    render_setup_script,
)
from osx_proxmox_next.arm64_planner import (
    IMAGE_DIR,
    host_key_path,
    build_arm64_destroy_plan,
    build_arm64_plan,
    key_path,
    snippet_path,
)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc runner@host"


def cfg(**kw) -> Arm64Config:
    base = dict(vmid=478, name="macos27-runner", storage="sn850x", source_ref=REF, source_sha256=SHA)
    base.update(kw)
    return Arm64Config(**base)


def titles(steps):
    return [s.title for s in steps]


def by_title(steps, title):
    return next(s for s in steps if s.title == title)


def test_the_plan_creates_a_tagged_runner_and_starts_it_last():
    steps = build_arm64_plan(cfg())
    assert titles(steps) == [
        "Check snippet storage", "Make the image folder", "Fetch the Debian 13 cloud image", "Create the runner VM",
        "Import the disk", "Grow the disk", "Write the cloud-init file", "Make the runner's SSH key", "Set up cloud-init",
        "Start the runner"]
    create = by_title(steps, "Create the runner VM").argv
    assert create[:5] == ["qm", "create", "478", "--name", "macos27-runner"]
    assert create[create.index("--cores") + 1] == "6" and create[create.index("--memory") + 1] == "8192"
    assert create[create.index("--tags") + 1] == ARM64_TAG and "--cpuunits" not in create
    assert create[create.index("--net0") + 1] == "virtio,bridge=vmbr0"
    assert steps[-1].argv == ["qm", "start", "478"]


def test_disk_comes_from_the_chosen_storage_and_is_grown():
    steps = build_arm64_plan(cfg(disk_gb=150))
    assert by_title(steps, "Import the disk").argv[3:5] == ["--scsi0", (
        f"sn850x:0,import-from={IMAGE_DIR}/debian-13-genericcloud-amd64.qcow2,discard=on,ssd=1,iothread=1")]
    assert by_title(steps, "Grow the disk").argv == ["qm", "resize", "478", "scsi0", "150G"]
    assert by_title(steps, "Set up cloud-init").argv[4] == "sn850x:cloudinit"


def test_cpu_weight_vlan_and_gpu_are_applied_only_when_asked():
    steps = build_arm64_plan(cfg(cpu_units=100, vlan=20, gpu_pci="0000:03:00.0"))
    create = by_title(steps, "Create the runner VM").argv
    assert create[create.index("--cpuunits") + 1] == "100"
    assert create[create.index("--net0") + 1] == "virtio,bridge=vmbr0,tag=20"
    gpu = by_title(steps, "Pass the GPU to the runner")
    assert gpu.argv == ["qm", "set", "478", "--hostpci0", "0000:03:00.0,pcie=1"] and gpu.risk == "warn"
    assert titles(steps).index("Pass the GPU to the runner") < titles(steps).index("Start the runner")
    assert "Pass the GPU to the runner" not in titles(build_arm64_plan(cfg()))


def test_the_ssh_key_goes_through_a_private_file_because_qm_reads_a_path():
    steps = build_arm64_plan(cfg(ssh_public_key=KEY))
    cloud = by_title(steps, "Set up cloud-init").argv
    assert cloud[cloud.index("--sshkeys") + 1] == "/var/lib/vz/snippets/osx-next-arm64-478.pub"
    write = by_title(steps, "Write the SSH key file")
    assert write.argv[4:] == [KEY, key_path(478)] and "umask 077" in write.argv[2] and "chmod 600" in write.argv[2]
    assert titles(steps).index("Write the SSH key file") < titles(steps).index("Set up cloud-init")
    assert "Make the runner's SSH key" not in titles(steps)   # the user's own key is used as given


def test_a_runner_without_a_given_key_gets_one_made_on_the_host(tmp_path):
    """Audit item: a wizard-made runner had no login at all. Run the step for real with ssh-keygen."""
    import os
    import stat
    import subprocess
    steps = build_arm64_plan(cfg())
    cloud = by_title(steps, "Set up cloud-init").argv
    assert cloud[cloud.index("--sshkeys") + 1] == key_path(478)
    assert titles(steps).index("Make the runner's SSH key") < titles(steps).index("Set up cloud-init")
    assert "Write the SSH key file" not in titles(steps)
    step = by_title(steps, "Make the runner's SSH key")
    assert step.argv[4:] == [host_key_path(478), key_path(478), "osx-next-arm64-478"]
    priv, out = tmp_path / "keys" / "k", tmp_path / "out.pub"
    argv = [*step.argv[:4], str(priv), str(out), "osx-next-arm64-478"]
    assert subprocess.run(argv, capture_output=True, text=True).returncode == 0
    assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600 and stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert out.read_text().startswith("ssh-ed25519 ") and out.read_text() == (tmp_path / "keys" / "k.pub").read_text()
    first = priv.read_text()
    assert subprocess.run(argv, capture_output=True, text=True).returncode == 0
    assert priv.read_text() == first, "a second run must keep the key"


def test_cloud_init_uses_vendor_data_so_proxmox_keeps_the_user_and_key():
    argv = by_title(build_arm64_plan(cfg()), "Set up cloud-init").argv
    assert argv[argv.index("--cicustom") + 1] == "vendor=local:snippets/osx-next-arm64-478.yaml"
    assert argv[argv.index("--ciuser") + 1] == "snapintosh" and "--cipassword" not in argv


def test_the_image_fetch_gets_its_urls_as_arguments_and_checks_the_checksum():
    step = by_title(build_arm64_plan(cfg()), "Fetch the Debian 13 cloud image")
    assert step.argv[:3] == ["bash", "-c", step.argv[2]] and step.argv[3] == "_"
    assert step.argv[4:] == [DEBIAN_IMAGE_URL, DEBIAN_SUMS_URL, f"{IMAGE_DIR}/debian-13-genericcloud-amd64.qcow2"]
    assert "sha512sum -c" in step.argv[2] and DEBIAN_IMAGE_URL not in step.argv[2]


def test_the_snippet_check_names_the_storage_as_an_argument():
    step = by_title(build_arm64_plan(cfg()), "Check snippet storage")
    assert step.argv[-1] == "local" and "pvesm status --content snippets" in step.argv[2]


def test_the_cloud_init_file_is_written_from_an_argument_to_the_snippet_path():
    step = by_title(build_arm64_plan(cfg()), "Write the cloud-init file")
    assert step.argv[4:] == [render_cloud_init(cfg()), "/var/lib/vz/snippets/osx-next-arm64-478.yaml"]
    assert snippet_path(478) == step.argv[-1] and 'printf "%s" "$1" > "$2"' in step.argv[2]


@pytest.mark.parametrize("bad", [
    dict(name="a;reboot"), dict(storage="x y"), dict(bridge="vmbr0 --foo"), dict(source_ref="x; id"),
    dict(vmid=5), dict(ssh_public_key="ssh-ed25519 AAAA ; id"),
])
def test_an_invalid_config_never_becomes_a_plan(bad):
    with pytest.raises(ValueError, match="Invalid arm64 config"):
        build_arm64_plan(cfg(**bad))


def test_the_setup_script_fetches_the_ref_and_runs_snapintosh_as_the_runner_user():
    script = render_setup_script(cfg(source_ref="a" * 40))
    assert f"archive/{'a' * 40}.tar.gz" in script
    assert f"{INSTALL_DIR}/snapintosh build --deps --apt" in script
    assert "build -j" in script and "build --only mesa" in script and "install" in script
    assert STATUS_PATH in script and LOG_PATH in script and "install-pending" in script
    assert script.count("runuser -l snapintosh") == 1   # one helper, every call goes through it


def test_the_install_inputs_become_arguments_and_the_script_stays_bash():
    import subprocess
    script = render_setup_script(cfg(ipsw="/var/lib/ipsw/a.ipsw", aux="/var/lib/vm/aux.img",
                                     ecid="4533301000242790675", disk_target="/var/lib/vm/install.raw"))
    assert ("snapintosh install --ipsw /var/lib/ipsw/a.ipsw --aux /var/lib/vm/aux.img "
            "--disk /var/lib/vm/install.raw --ecid 4533301000242790675 || rc=$?") in script
    assert "snapintosh install || rc=$?" in render_setup_script(cfg())
    check = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert check.returncode == 0, check.stderr


def test_the_setup_script_sets_home_and_refuses_an_empty_package_list():
    script = render_setup_script(cfg())
    assert script.index("export HOME=/root") < script.index("build --deps --apt")   # systemd-run gives it none
    assert 'packages=$(/opt/osx-next-arm64/snapintosh build --deps --apt)' in script
    assert '[ -n "$packages" ]' in script and "apt-get install -y --no-install-recommends $packages" in script


def test_the_setup_script_survives_a_run_without_home(tmp_path):
    """The regression of the first live run: with no HOME the listing came back empty and nothing was installed."""
    import subprocess
    fake = tmp_path / "snapintosh"
    fake.write_text('#!/bin/bash\nset -u\necho "$HOME" > "$0.home"\n')
    fake.chmod(0o755)
    probe = render_setup_script(cfg()).split("say installing-packages")[0].split("exec >>")[0]
    out = subprocess.run(["env", "-u", "HOME", "bash", "-c", probe + f'{fake}\n'], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "snapintosh.home").read_text().strip() == "/root"


def test_the_runner_gets_a_swap_file_before_the_build_when_it_has_none():
    script = render_setup_script(cfg())
    assert "fallocate -l 4G /swapfile" in script and "chmod 600 /swapfile" in script and "swapon --show" in script
    assert script.index("say adding-swap") < script.index("say building")
    assert "|| echo" in script.split("say adding-swap")[1].split("say installing-packages")[0], "a failing swap file must not stop the build"


def test_the_setup_script_is_valid_bash():
    import subprocess
    check = subprocess.run(["bash", "-n"], input=render_setup_script(cfg()), text=True, capture_output=True)
    assert check.returncode == 0, check.stderr


def test_no_secret_reaches_the_cloud_init_file():
    text = render_cloud_init(cfg(ssh_public_key=KEY))
    assert KEY not in text and "AAAAC3" not in text   # the key goes through qm --sshkeys, not the snippet
    for word in ("password", "passwd", "chpasswd", "vnc"):
        assert word not in text.lower().replace("snapintosh", "")


def test_the_cloud_config_installs_the_agent_and_starts_the_setup_detached():
    text = render_cloud_init(cfg())
    assert text.startswith("#cloud-config\n") and "qemu-guest-agent" in text
    assert "systemd-run, --unit=osx-next-arm64, --no-block, /usr/local/sbin/osx-next-arm64-setup" in text
    assert "    content: |\n      #!/bin/bash\n" in text and "\n\n" in text   # blank lines of the script survive


def test_destroy_purges_the_vm_and_removes_what_the_plan_left():
    steps = build_arm64_destroy_plan(478)
    assert titles(steps) == ["Check the VM is a runner made by this wizard", "Stop VM", "Destroy VM",
                             "Remove the cloud-init files", "Remove the downloaded image"]
    assert steps[2].argv == ["qm", "destroy", "478", "--purge"]
    assert steps[3].argv == ["rm", "-f", "/var/lib/vz/snippets/osx-next-arm64-478.yaml",
                             "/var/lib/vz/snippets/osx-next-arm64-478.pub", "/root/.ssh/osx-next-arm64-478",
                             "/root/.ssh/osx-next-arm64-478.pub"]
    assert steps[4].argv == ["rm", "-rf", IMAGE_DIR]


@pytest.mark.parametrize("vmid", [0, 99, 1_000_000])
def test_destroy_refuses_a_vmid_out_of_range(vmid):
    with pytest.raises(ValueError, match="VMID"):
        build_arm64_destroy_plan(vmid)


def test_the_setup_script_traps_errors_in_functions_and_checks_the_rustup_download():
    script = render_setup_script(cfg())
    assert "set -Eeuo pipefail" in script and "set -o pipefail; curl -fsSL https://sh.rustup.rs" in script


def _stub_bin(tmp_path, **tools):
    """A PATH directory of scripts (name -> shell text) that stand in for qm, curl and friends."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, text in tools.items():
        (bin_dir / name).write_text("#!/bin/bash\n" + text)
        (bin_dir / name).chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin"}


def _run_step(step, env, *replace):
    import subprocess
    argv = list(step.argv)
    for idx, value in replace:
        argv[idx] = value
    return subprocess.run(argv, env=env, capture_output=True, text=True)


def test_the_source_archive_is_checked_against_its_sha256_before_it_is_unpacked():
    script = render_setup_script(cfg())
    assert f"echo {SHA}" in script and "sha256sum -c -" in script
    assert script.index("sha256sum -c -") < script.index("tar -xzf")
    assert "main" not in script.replace("remain", "")


def test_a_mismatching_source_archive_stops_the_script_and_unpacks_nothing(tmp_path):
    """Run the fetch part of the setup script with a curl that serves an archive whose checksum is not the pinned one."""
    import subprocess
    script = render_setup_script(cfg())
    head = script.split("say installing-packages")[0].replace("exec >>", "#exec >>")
    head = head.replace("/opt/osx-next-arm64", str(tmp_path / "opt")).replace("chown -R snapintosh:snapintosh", "true")
    head = head.replace("/var/lib/osx-next-arm64/status", str(tmp_path / "status")).replace("/var/log/osx-next-arm64.log", str(tmp_path / "log"))
    env = _stub_bin(tmp_path, curl='while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done; echo hostile > "$out"\n')
    result = subprocess.run(["bash", "-c", head], env={**env, "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode != 0 and "does not match its checksum" in result.stderr
    assert not list((tmp_path / "opt").glob("*"))


def test_the_renderer_override_reaches_the_build_only_when_set():
    plain = render_setup_script(cfg())
    assert "RENDERER_URL" not in plain
    custom = render_setup_script(cfg(renderer_url="http://192.168.1.50:8000/r.tar.gz", renderer_sha256=RENDERER_SHA))
    assert f"as_user env RENDERER_URL=http://192.168.1.50:8000/r.tar.gz RENDERER_SHA256={RENDERER_SHA} " in custom
    assert "build -j" in custom.split("RENDERER_SHA256=")[1]


def test_a_cached_debian_image_that_is_out_of_date_is_replaced(tmp_path):
    """Audit item: the cache was trusted by name. A stale file must be downloaded again and the checksum checked after."""
    import hashlib
    new = b"new image"
    want = hashlib.sha512(new).hexdigest()
    env = _stub_bin(tmp_path, curl=(
        'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift;; http*) url=$1;; esac; shift; done\n'
        f'case "$url" in *SHA512SUMS) echo "{want}  debian-13-genericcloud-amd64.qcow2";; *) printf %s "{new.decode()}" > "$out";; esac\n'))
    step = by_title(build_arm64_plan(cfg()), "Fetch the Debian 13 cloud image")
    dest = tmp_path / "debian-13-genericcloud-amd64.qcow2"
    dest.write_bytes(b"old image")
    result = _run_step(step, env, (6, str(dest)))
    assert result.returncode == 0, result.stderr + result.stdout
    assert "out of date" in result.stdout and dest.read_bytes() == new
    again = _run_step(step, env, (6, str(dest)))
    assert again.returncode == 0 and "out of date" not in again.stdout


def test_a_download_that_does_not_match_the_published_checksum_fails(tmp_path):
    env = _stub_bin(tmp_path, curl=(
        'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift;; http*) url=$1;; esac; shift; done\n'
        'case "$url" in *SHA512SUMS) echo "' + "0" * 128 + '  debian-13-genericcloud-amd64.qcow2";; *) echo tampered > "$out";; esac\n'))
    step = by_title(build_arm64_plan(cfg()), "Fetch the Debian 13 cloud image")
    result = _run_step(step, env, (6, str(tmp_path / "debian-13-genericcloud-amd64.qcow2")))
    assert result.returncode != 0 and "FAILED" in result.stdout + result.stderr


def test_a_vm_without_the_wizard_tag_is_never_stopped_or_destroyed(tmp_path):
    """Audit item: `arm64 uninstall --vmid N` ran qm stop and qm destroy on any VM. The first step now checks the tag."""
    import subprocess
    check = build_arm64_destroy_plan(478)[0]
    assert check.argv[4:] == ["478", ARM64_TAG]
    def run(tags: str):
        line = f'echo "tags: {tags}"\n' if tags else "true\n"
        env = _stub_bin(tmp_path, qm=f'[ "$1" = config ] && {{ echo "cores: 6"; {line.strip()}; }}\n')
        return subprocess.run(check.argv, env=env, capture_output=True, text=True)
    assert run(f"{ARM64_TAG}").returncode == 0
    assert run(f"other;{ARM64_TAG}").returncode == 0 and run(f"a,{ARM64_TAG},b").returncode == 0
    for tags in ("", "prod", f"not-{ARM64_TAG}", f"{ARM64_TAG}-x"):
        result = run(tags)
        assert result.returncode == 1 and "not made by this wizard" in result.stderr, tags


def test_the_tag_check_comes_before_anything_that_changes_the_vm():
    steps = build_arm64_destroy_plan(478)
    assert steps[0].risk == "safe" and steps[0].argv[0] == "bash"
    assert all(s.argv[0] != "qm" for s in steps[:1])
