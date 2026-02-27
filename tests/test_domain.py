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


def test_validate_config_accepts_ventura() -> None:
    cfg = VmConfig(
        vmid=900,
        name="macos-ventura",
        macos="ventura",
        cores=4,
        memory_mb=8192,
        disk_gb=80,
        bridge="vmbr0",
        storage="local-lvm",
    )
    assert validate_config(cfg) == []


def _valid_cfg(**overrides) -> VmConfig:
    defaults = dict(
        vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
        memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm",
    )
    defaults.update(overrides)
    return VmConfig(**defaults)


def test_validate_smbios_serial_valid() -> None:
    cfg = _valid_cfg(smbios_serial="C02N1000P7QM")
    assert validate_config(cfg) == []


def test_validate_smbios_serial_rejects_bad_charset() -> None:
    cfg = _valid_cfg(smbios_serial="C02';rm -rf /")
    issues = validate_config(cfg)
    assert any("serial" in i for i in issues)


def test_validate_smbios_serial_rejects_wrong_length() -> None:
    cfg = _valid_cfg(smbios_serial="SHORT")
    issues = validate_config(cfg)
    assert any("serial" in i for i in issues)


def test_validate_smbios_mlb_valid() -> None:
    cfg = _valid_cfg(smbios_mlb="C02901403QVK3F708")
    assert validate_config(cfg) == []


def test_validate_smbios_mlb_rejects_bad_charset() -> None:
    cfg = _valid_cfg(smbios_mlb="C02901403QVK3F70!")
    issues = validate_config(cfg)
    assert any("MLB" in i for i in issues)


def test_validate_smbios_rom_valid() -> None:
    cfg = _valid_cfg(smbios_rom="02ABCDEF0123")
    assert validate_config(cfg) == []


def test_validate_smbios_rom_rejects_lowercase() -> None:
    cfg = _valid_cfg(smbios_rom="02abcdef0123")
    issues = validate_config(cfg)
    assert any("ROM" in i for i in issues)


def test_validate_smbios_rom_rejects_non_hex() -> None:
    cfg = _valid_cfg(smbios_rom="02ABCDEG0123")
    issues = validate_config(cfg)
    assert any("ROM" in i for i in issues)


def test_validate_smbios_uuid_valid() -> None:
    cfg = _valid_cfg(smbios_uuid="550E8400-E29B-41D4-A716-446655440000")
    assert validate_config(cfg) == []


def test_validate_smbios_uuid_rejects_bad_format() -> None:
    cfg = _valid_cfg(smbios_uuid="not-a-uuid")
    issues = validate_config(cfg)
    assert any("UUID" in i for i in issues)


def test_validate_smbios_model_valid() -> None:
    cfg = _valid_cfg(smbios_model="MacPro7,1")
    assert validate_config(cfg) == []


def test_validate_smbios_model_rejects_injection() -> None:
    cfg = _valid_cfg(smbios_model="MacPro7,1';echo pwned")
    issues = validate_config(cfg)
    assert any("model" in i for i in issues)


def test_validate_smbios_empty_fields_ok() -> None:
    """Empty SMBIOS fields are fine — auto-generated at plan time."""
    cfg = _valid_cfg()
    assert validate_config(cfg) == []


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
