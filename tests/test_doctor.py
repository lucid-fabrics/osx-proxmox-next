from __future__ import annotations

import pytest

from osx_proxmox_next.doctor import (
    DoctorCheck,
    Severity,
    _check_agent,
    _check_balloon,
    _check_boot_order,
    _check_cores,
    _check_cpu,
    _check_disk,
    _check_machine,
    _check_memory,
    _check_net,
    _check_smbios,
    _is_power_of_two,
    _net_model_from_value,
    _parse_qm_config,
    run_doctor,
)
from osx_proxmox_next.infrastructure import CommandResult, ProxmoxAdapter


# ---------------------------------------------------------------------------
# _parse_qm_config
# ---------------------------------------------------------------------------

def test_parse_qm_config_basic():
    raw = "balloon: 0\nmachine: pc-q35-8.1+pve0\ncores: 4\n"
    cfg = _parse_qm_config(raw)
    assert cfg["balloon"] == "0"
    assert cfg["machine"] == "pc-q35-8.1+pve0"
    assert cfg["cores"] == "4"


def test_parse_qm_config_skips_comments():
    raw = "# this is a comment\ncores: 8\n"
    cfg = _parse_qm_config(raw)
    assert "cores" in cfg
    assert len(cfg) == 1


def test_parse_qm_config_empty():
    assert _parse_qm_config("") == {}


def test_parse_qm_config_strips_whitespace():
    raw = "  cpu :  host  \n"
    cfg = _parse_qm_config(raw)
    assert cfg["cpu"] == "host"


# ---------------------------------------------------------------------------
# _is_power_of_two
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 4, 8, 16, 32])
def test_is_power_of_two_true(n):
    assert _is_power_of_two(n) is True


@pytest.mark.parametrize("n", [3, 5, 6, 7, 9, 10])
def test_is_power_of_two_false(n):
    assert _is_power_of_two(n) is False


def test_is_power_of_two_zero():
    assert _is_power_of_two(0) is False


# ---------------------------------------------------------------------------
# _net_model_from_value
# ---------------------------------------------------------------------------

def test_net_model_from_value_vmxnet3():
    assert _net_model_from_value("vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0") == "vmxnet3"


def test_net_model_from_value_e1000():
    assert _net_model_from_value("e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr0") == "e1000"


def test_net_model_from_value_no_mac():
    assert _net_model_from_value("virtio,bridge=vmbr0") == "virtio"


# ---------------------------------------------------------------------------
# _check_balloon
# ---------------------------------------------------------------------------

def test_check_balloon_ok():
    check = _check_balloon({"balloon": "0"}, 100)
    assert check.severity == Severity.OK


def test_check_balloon_fail_nonzero():
    check = _check_balloon({"balloon": "1"}, 100)
    assert check.severity == Severity.FAIL
    assert "qm set 100 --balloon 0" in check.fix


def test_check_balloon_fail_not_set():
    check = _check_balloon({}, 100)
    assert check.severity == Severity.FAIL
    assert "not set" in check.message


# ---------------------------------------------------------------------------
# _check_machine
# ---------------------------------------------------------------------------

def test_check_machine_ok():
    check = _check_machine({"machine": "pc-q35-8.1+pve0"}, 100)
    assert check.severity == Severity.OK


def test_check_machine_fail_pc():
    check = _check_machine({"machine": "pc-i440fx-8.1"}, 100)
    assert check.severity == Severity.FAIL
    assert "qm set 100 --machine q35" in check.fix


def test_check_machine_fail_not_set():
    check = _check_machine({}, 100)
    assert check.severity == Severity.FAIL


# ---------------------------------------------------------------------------
# _check_cores
# ---------------------------------------------------------------------------

def test_check_cores_ok_power_of_two():
    check = _check_cores({"cores": "4"}, 100)
    assert check.severity == Severity.OK


def test_check_cores_fail_odd():
    check = _check_cores({"cores": "3"}, 100)
    assert check.severity == Severity.FAIL
    assert "qm set 100 --cores" in check.fix


def test_check_cores_warn_not_set():
    check = _check_cores({}, 100)
    assert check.severity == Severity.WARN


def test_check_cores_warn_invalid_string():
    check = _check_cores({"cores": "notanumber"}, 100)
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# _check_memory
# ---------------------------------------------------------------------------

def test_check_memory_ok():
    check = _check_memory({"memory": "8192"}, 100)
    assert check.severity == Severity.OK


def test_check_memory_warn_too_low():
    check = _check_memory({"memory": "2048"}, 100)
    assert check.severity == Severity.WARN
    assert "qm set 100 --memory 4096" in check.fix


def test_check_memory_warn_not_set():
    check = _check_memory({}, 100)
    assert check.severity == Severity.WARN


def test_check_memory_warn_invalid():
    check = _check_memory({"memory": "bad"}, 100)
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# _check_cpu
# ---------------------------------------------------------------------------

def test_check_cpu_ok_host():
    check = _check_cpu({"cpu": "host"})
    assert check.severity == Severity.OK


def test_check_cpu_ok_cascadelake():
    check = _check_cpu({"cpu": "Cascadelake-Server"})
    assert check.severity == Severity.OK


def test_check_cpu_warn_kvm64():
    check = _check_cpu({"cpu": "kvm64"})
    assert check.severity == Severity.WARN


def test_check_cpu_warn_kvm32():
    check = _check_cpu({"cpu": "kvm32"})
    assert check.severity == Severity.WARN


def test_check_cpu_warn_not_set():
    check = _check_cpu({})
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# _check_net
# ---------------------------------------------------------------------------

def test_check_net_ok_vmxnet3():
    check = _check_net({"net0": "vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0"}, 100)
    assert check.severity == Severity.OK


def test_check_net_ok_e1000():
    check = _check_net({"net0": "e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr0"}, 100)
    assert check.severity == Severity.OK


def test_check_net_ok_e1000_82545em():
    check = _check_net({"net0": "e1000-82545em=AA:BB:CC:DD:EE:FF,bridge=vmbr0"}, 100)
    assert check.severity == Severity.OK


def test_check_net_fail_virtio():
    check = _check_net({"net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0"}, 100)
    assert check.severity == Severity.FAIL
    assert "qm set 100 --net0 vmxnet3" in check.fix


def test_check_net_warn_not_configured():
    check = _check_net({}, 100)
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# _check_agent
# ---------------------------------------------------------------------------

def test_check_agent_ok():
    check = _check_agent({"agent": "enabled=1,fstrim_cloned_disks=0"}, 100)
    assert check.severity == Severity.OK


def test_check_agent_warn_not_enabled():
    check = _check_agent({"agent": "enabled=0"}, 100)
    assert check.severity == Severity.WARN
    assert "qm set 100 --agent enabled=1" in check.fix


def test_check_agent_warn_not_set():
    check = _check_agent({}, 100)
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# _check_smbios
# ---------------------------------------------------------------------------

def test_check_smbios_ok():
    check = _check_smbios({"smbios1": "uuid=AAAA-BBBB,serial=C02XG0FDH7JY"})
    assert check.severity == Severity.OK


def test_check_smbios_warn_not_set():
    check = _check_smbios({})
    assert check.severity == Severity.WARN


def test_check_smbios_warn_no_uuid():
    check = _check_smbios({"smbios1": "serial=onlythis"})
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# _check_disk
# ---------------------------------------------------------------------------

def test_check_disk_ok():
    check = _check_disk({"virtio0": "local-lvm:vm-100-disk-0"}, "virtio0", "main disk")
    assert check.severity == Severity.OK


def test_check_disk_warn_missing():
    check = _check_disk({}, "virtio0", "main disk")
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# _check_boot_order
# ---------------------------------------------------------------------------

def test_check_boot_order_ok():
    check = _check_boot_order({"boot": "order=ide2;virtio0;ide0"}, 100)
    assert check.severity == Severity.OK


def test_check_boot_order_fail_ide3():
    check = _check_boot_order({"boot": "order=ide3;virtio0;ide0"}, 100)
    assert check.severity == Severity.FAIL
    assert "qm set 100 --boot order=ide2;virtio0;ide0" in check.fix


def test_check_boot_order_warn_not_set():
    check = _check_boot_order({}, 100)
    assert check.severity == Severity.WARN


# ---------------------------------------------------------------------------
# run_doctor
# ---------------------------------------------------------------------------

_GOOD_CONFIG = "\n".join([
    "balloon: 0",
    "machine: pc-q35-8.1+pve0",
    "cores: 4",
    "memory: 8192",
    "cpu: host",
    "net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0",
    "agent: enabled=1",
    "smbios1: uuid=AAAA-BBBB,serial=C02XG0FDH7JY",
    "boot: order=ide2;virtio0;ide0",
    "virtio0: local-lvm:vm-100-disk-0",
    "ide0: local:iso/OpenCore.iso,media=disk",
    "ide2: local:iso/BaseSystem.img,media=disk",
])


class _FakeAdapter(ProxmoxAdapter):
    def __init__(self, config_output: str, ok: bool = True):
        self._config = config_output
        self._ok = ok

    def qm(self, *args: str) -> CommandResult:
        return CommandResult(ok=self._ok, returncode=0 if self._ok else 1, output=self._config)


def test_run_doctor_all_ok():
    adapter = _FakeAdapter(_GOOD_CONFIG)
    checks = run_doctor(100, adapter=adapter)
    assert all(c.severity == Severity.OK for c in checks)


def test_run_doctor_returns_twelve_checks():
    adapter = _FakeAdapter(_GOOD_CONFIG)
    checks = run_doctor(100, adapter=adapter)
    assert len(checks) == 12


def test_run_doctor_vm_not_found():
    adapter = _FakeAdapter("VM 999 not found", ok=False)
    checks = run_doctor(999, adapter=adapter)
    assert len(checks) == 1
    assert checks[0].severity == Severity.FAIL
    assert "999" in checks[0].message


def test_run_doctor_detects_bad_balloon():
    config = _GOOD_CONFIG.replace("balloon: 0", "balloon: 1")
    adapter = _FakeAdapter(config)
    checks = run_doctor(100, adapter=adapter)
    balloon_check = next(c for c in checks if c.name == "balloon")
    assert balloon_check.severity == Severity.FAIL


def test_run_doctor_detects_bad_machine():
    config = _GOOD_CONFIG.replace("machine: pc-q35-8.1+pve0", "machine: pc-i440fx-8.1")
    adapter = _FakeAdapter(config)
    checks = run_doctor(100, adapter=adapter)
    machine_check = next(c for c in checks if c.name == "machine")
    assert machine_check.severity == Severity.FAIL


def test_run_doctor_detects_odd_cores():
    config = _GOOD_CONFIG.replace("cores: 4", "cores: 3")
    adapter = _FakeAdapter(config)
    checks = run_doctor(100, adapter=adapter)
    cores_check = next(c for c in checks if c.name == "cores")
    assert cores_check.severity == Severity.FAIL
