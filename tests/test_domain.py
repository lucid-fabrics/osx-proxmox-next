from osx_proxmox_next.domain import VmConfig, validate_config


def test_validate_config_accepts_sequoia_defaults() -> None:
    cfg = VmConfig(
        vmid=900,
        name="macos-sequoia",
        macos="sequoia",
        cores=8,
        memory_mb=16384,
        disk_gb=128,
        bridge="vmbr0",
        storage="local-lvm",
    )
    assert validate_config(cfg) == []


def test_validate_config_rejects_invalid_values() -> None:
    cfg = VmConfig(
        vmid=5,
        name="x",
        macos="unknown",
        cores=1,
        memory_mb=2048,
        disk_gb=32,
        bridge="br0",
        storage="",
    )
    issues = validate_config(cfg)
    assert len(issues) >= 7
    assert any("VMID" in issue for issue in issues)
    assert any("macOS version" in issue for issue in issues)


def test_validate_tahoe_no_installer_path_ok():
    cfg = VmConfig(
        vmid=900,
        name="macos-tahoe",
        macos="tahoe",
        cores=8,
        memory_mb=16384,
        disk_gb=160,
        bridge="vmbr0",
        storage="local-lvm",
        installer_path="",
    )
    issues = validate_config(cfg)
    assert not any("Tahoe" in i for i in issues)
