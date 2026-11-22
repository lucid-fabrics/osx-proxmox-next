"""The arm64 (beta) runner plan: qm and shell steps, cloud-init files, and nothing spliced into a shell."""
from __future__ import annotations

import pytest

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
    build_arm64_destroy_plan,
    build_arm64_plan,
    key_path,
    snippet_path,
)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc runner@host"


def cfg(**kw) -> Arm64Config:
    base = dict(vmid=478, name="macos27-runner", storage="sn850x")
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
        "Import the disk", "Grow the disk", "Write the cloud-init file", "Set up cloud-init", "Start the runner"]
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
    assert write.argv[4:] == [KEY, key_path(478)] and "umask 077" in write.argv[2]
    assert titles(steps).index("Write the SSH key file") < titles(steps).index("Set up cloud-init")
    plain = build_arm64_plan(cfg())
    assert "--sshkeys" not in by_title(plain, "Set up cloud-init").argv and "Write the SSH key file" not in titles(plain)


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
    script = render_setup_script(cfg(source_ref="feat/arm64-macos27-beta"))
    assert "archive/feat/arm64-macos27-beta.tar.gz" in script
    assert f"{INSTALL_DIR}/snapintosh build --deps --apt" in script
    assert "build -j" in script and "build --only mesa" in script and "install" in script
    assert STATUS_PATH in script and LOG_PATH in script and "install-pending" in script
    assert script.count("runuser -l snapintosh") == 1   # one helper, every call goes through it


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
    assert titles(steps) == ["Stop VM", "Destroy VM", "Remove the cloud-init files", "Remove the downloaded image"]
    assert steps[1].argv == ["qm", "destroy", "478", "--purge"]
    assert steps[2].argv == ["rm", "-f", "/var/lib/vz/snippets/osx-next-arm64-478.yaml",
                             "/var/lib/vz/snippets/osx-next-arm64-478.pub"]
    assert steps[3].argv == ["rm", "-rf", IMAGE_DIR]


@pytest.mark.parametrize("vmid", [0, 99, 1_000_000])
def test_destroy_refuses_a_vmid_out_of_range(vmid):
    with pytest.raises(ValueError, match="VMID"):
        build_arm64_destroy_plan(vmid)
