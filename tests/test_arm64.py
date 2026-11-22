"""The arm64 (beta) config: every field that reaches a qm argument or a cloud-init file is restricted."""
from __future__ import annotations

import dataclasses

import pytest

from osx_proxmox_next.arm64 import DEFAULT_SOURCE_URL, Arm64Config, validate_arm64_config


def good(**kw) -> Arm64Config:
    base = dict(vmid=478, name="macos27-runner", storage="local-lvm")
    base.update(kw)
    return Arm64Config(**base)


def test_the_defaults_are_valid_and_sized_for_the_beta():
    cfg = good()
    assert validate_arm64_config(cfg) == []
    assert (cfg.cores, cfg.memory_mb, cfg.disk_gb, cfg.vlan, cfg.cpu_units) == (6, 8192, 120, 0, 0)
    assert cfg.gpu_pci == "" and cfg.ssh_public_key == ""


def test_the_archive_url_names_the_ref():
    assert good(source_ref="feat/arm64-macos27-beta").archive_url == (
        "https://github.com/lucid-fabrics/osx-proxmox-next/archive/feat/arm64-macos27-beta.tar.gz")
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
    (dict(source_url="http://example.com/{ref}.tar.gz"), "Source URL"),
    (dict(source_url="https://example.com/a b/{ref}"), "Source URL"),
    (dict(source_url="https://example.com/$(id)"), "Source URL"),
    (dict(source_ref=""), "Source ref"), (dict(source_ref="a..b"), "Source ref"),
    (dict(source_ref="main; id"), "Source ref"), (dict(source_ref="-x"), "Source ref"),
])
def test_each_bad_value_is_refused(change, needle):
    issues = validate_arm64_config(good(**change))
    assert any(needle in issue for issue in issues), issues


@pytest.mark.parametrize("change", [
    dict(vlan=100), dict(cpu_units=100), dict(gpu_pci="0000:03:00.0"), dict(gpu_pci="03:00.0"),
    dict(ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc user@host"),
    dict(ssh_public_key="ecdsa-sha2-nistp256 AAAAE2VjZHNh=="), dict(source_ref="a1b2c3d4"),
    dict(source_url="https://example.com:8443/archive/{ref}.tar.gz"),
])
def test_good_optional_values_pass(change):
    assert validate_arm64_config(good(**change)) == []


def test_the_config_is_a_plain_dataclass():
    assert {f.name for f in dataclasses.fields(Arm64Config)} >= {"vmid", "name", "storage", "gpu_pci", "cpu_units"}
