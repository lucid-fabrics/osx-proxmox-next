"""The arm64 (beta) config: every field that reaches a qm argument or a cloud-init file is restricted."""
from __future__ import annotations

import dataclasses

import pytest

from arm64_fakes import REF, SHA
from osx_proxmox_next.arm64 import DEFAULT_SOURCE_URL, Arm64Config, validate_arm64_config


def good(**kw) -> Arm64Config:
    base = dict(vmid=478, name="macos27-runner", storage="local-lvm", source_ref=REF, source_sha256=SHA)
    base.update(kw)
    return Arm64Config(**base)


def test_the_defaults_are_valid_and_sized_for_the_beta():
    cfg = good()
    assert validate_arm64_config(cfg) == []
    assert (cfg.cores, cfg.memory_mb, cfg.disk_gb, cfg.vlan, cfg.cpu_units) == (6, 8192, 120, 0, 0)
    assert cfg.gpu_pci == "" and cfg.ssh_public_key == ""


def test_the_archive_url_names_the_ref():
    assert good(source_ref="v0.9.1").archive_url == (
        "https://github.com/lucid-fabrics/osx-proxmox-next/archive/v0.9.1.tar.gz")
    assert "{ref}" in DEFAULT_SOURCE_URL


@pytest.mark.parametrize("change, needle", [
    (dict(vmid=99), "VMID"), (dict(vmid=1_000_000), "VMID"),
    (dict(name="ab"), "VM name"), (dict(name="x" * 64), "VM name"), (dict(name="a b;rm"), "VM name"),
    (dict(name="-bad-"), "VM name"),
    (dict(storage=""), "Storage"), (dict(storage="a b"), "Storage"), (dict(storage="x;y"), "Storage"),
    (dict(cores=3), "CPU cores"), (dict(memory_mb=4096), "RAM"), (dict(disk_gb=99), "disk"),
    (dict(bridge="br0"), "Bridge"), (dict(bridge="vmbr0,firewall=1"), "Bridge"),
    (dict(vlan=4095), "VLAN"), (dict(vlan=-1), "VLAN"),
    (dict(cpu_units=10001), "CPU units"), (dict(cpu_units=-5), "CPU units"),
    (dict(ssh_public_key="ssh-ed25519 AAAA\nssh-rsa BBBB"), "SSH public key"),
    (dict(ssh_public_key="not a key"), "SSH public key"),
    (dict(ssh_public_key="ssh-ed25519 AAAA $(id)"), "SSH public key"),
    (dict(gpu_pci="03:00"), "GPU address"), (dict(gpu_pci="0000:03:00.0,x-vga=1"), "GPU address"),
    (dict(source_url="ftp://example.com/{ref}.tar.gz"), "Source URL"),
    (dict(source_url="https://example.com/a b/{ref}"), "Source URL"),
    (dict(source_url="https://example.com/$(id)"), "Source URL"),
    (dict(source_ref=""), "Source ref"), (dict(source_ref="a..b"), "Source ref"),
    (dict(source_ref="main; id"), "Source ref"), (dict(source_ref="-x"), "Source ref"),
    (dict(source_ref="main"), "not a branch"), (dict(source_ref="feat/arm64-macos27-beta"), "not a branch"),
    (dict(source_ref="a1b2c3d4"), "not a branch"), (dict(source_ref="A" * 40), "not a branch"),
    (dict(source_sha256=""), "Source sha256"), (dict(source_sha256="a" * 63), "Source sha256"),
    (dict(source_sha256="G" * 64), "Source sha256"),
    (dict(renderer_url="https://example.com/r.tar.gz"), "together"),
    (dict(renderer_sha256="b" * 64), "together"),
    (dict(renderer_url="https://example.com/r;id", renderer_sha256="b" * 64), "Renderer URL"),
    (dict(renderer_url="https://example.com/r.tar.gz", renderer_sha256="xyz"), "Renderer sha256"),
    (dict(ipsw="relative/a.ipsw"), "IPSW"), (dict(ipsw="/a/../b.ipsw"), "IPSW"),
    (dict(aux="/opt/a b.img"), "Aux image"), (dict(disk_target="d.raw"), "Disk target"),
    (dict(ecid="abc"), "ECID"), (dict(ecid="0"), "ECID"), (dict(ecid="1" * 21), "ECID"),
    (dict(ecid=str(2**64)), "ECID"),
])
def test_each_bad_value_is_refused(change, needle):
    issues = validate_arm64_config(good(**change))
    assert any(needle in issue for issue in issues), issues


@pytest.mark.parametrize("change", [
    dict(vlan=100), dict(cpu_units=100), dict(gpu_pci="0000:03:00.0"), dict(gpu_pci="03:00.0"),
    dict(ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc user@host"),
    dict(ssh_public_key="ecdsa-sha2-nistp256 AAAAE2VjZHNh=="), dict(source_ref="a" * 40), dict(source_ref="v1.2"),
    dict(source_url="http://192.168.1.50:8000/{ref}.tar.gz"),
    dict(renderer_url="file:///srv/r/arm64-renderer-1.tar.gz", renderer_sha256="b" * 64),
    dict(renderer_url="https://example.com/r.tar.gz", renderer_sha256="b" * 64),
    dict(source_url="https://example.com:8443/archive/{ref}.tar.gz"),
    dict(ipsw="/var/lib/ipsw/UniversalMac_27.2.1_26B131_Restore.ipsw"),
    dict(aux="/var/lib/vm/aux.img"), dict(ecid="4533301000242790675"),
    dict(disk_target="/var/lib/vm/install.raw"),
])
def test_good_optional_values_pass(change):
    assert validate_arm64_config(good(**change)) == []


def test_the_defaults_name_no_branch_and_a_checksum_is_mandatory():
    from osx_proxmox_next.arm64 import DEFAULT_SOURCE_REF, DEFAULT_SOURCE_SHA256
    assert DEFAULT_SOURCE_REF not in ("main", "master", "HEAD")
    pinned = Arm64Config(vmid=478, name="macos27-runner", storage="local-lvm")
    assert validate_arm64_config(pinned) == []
    unpinned = Arm64Config(vmid=478, name="macos27-runner", storage="local-lvm",
                           source_ref="", source_sha256="")
    assert validate_arm64_config(unpinned), "an unpinned config must be refused"
    assert not DEFAULT_SOURCE_SHA256 or len(DEFAULT_SOURCE_SHA256) == 64


def test_the_release_pin_is_set_or_the_test_says_wave_b_has_not_tagged_yet():
    """Wave B sets DEFAULT_SOURCE_REF and DEFAULT_SOURCE_SHA256; until then the pair is empty and this is skipped."""
    from osx_proxmox_next.arm64 import DEFAULT_SOURCE_REF, DEFAULT_SOURCE_SHA256, DEFAULT_SOURCE_URL
    assert bool(DEFAULT_SOURCE_REF) == bool(DEFAULT_SOURCE_SHA256), "the two defaults are set together"
    if not DEFAULT_SOURCE_REF:
        pytest.skip("Wave B has not tagged the beta yet")
    assert "{ref}" in DEFAULT_SOURCE_URL, "the archive URL must name the ref at download time"
    assert DEFAULT_SOURCE_REF.startswith("v") or len(DEFAULT_SOURCE_REF) == 40, DEFAULT_SOURCE_REF
    assert len(DEFAULT_SOURCE_SHA256) == 64, DEFAULT_SOURCE_SHA256
    assert int(DEFAULT_SOURCE_SHA256, 16) > 0, "an all-zero sha256 pins nothing"
    cfg = Arm64Config(vmid=478, name="macos27-runner", storage="local-lvm")
    assert validate_arm64_config(cfg) == [], "the default ref must pass the release tag or commit check"
    assert cfg.source_ref in cfg.archive_url and "{ref}" not in cfg.archive_url


def test_the_config_is_a_plain_dataclass():
    assert {f.name for f in dataclasses.fields(Arm64Config)} >= {"vmid", "name", "storage", "gpu_pci", "cpu_units"}
