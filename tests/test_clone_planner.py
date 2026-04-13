from __future__ import annotations

import re

import pytest

from osx_proxmox_next.planner import _parse_net0, build_clone_plan


# ── _parse_net0 ──────────────────────────────────────────────────────


def test_parse_net0_returns_defaults_when_none():
    bridge, model = _parse_net0(None)
    assert bridge == "vmbr0"
    assert model == "vmxnet3"


def test_parse_net0_returns_defaults_when_empty():
    bridge, model = _parse_net0("")
    assert bridge == "vmbr0"
    assert model == "vmxnet3"


def test_parse_net0_with_static_mac():
    raw = "name: macos-test\nnet0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr1,firewall=0\n"
    bridge, model = _parse_net0(raw)
    assert bridge == "vmbr1"
    assert model == "vmxnet3"


def test_parse_net0_without_mac():
    raw = "net0: vmxnet3,bridge=vmbr2,firewall=0\n"
    bridge, model = _parse_net0(raw)
    assert bridge == "vmbr2"
    assert model == "vmxnet3"


def test_parse_net0_e1000_model():
    raw = "net0: e1000-82545em=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0\n"
    bridge, model = _parse_net0(raw)
    assert bridge == "vmbr0"
    assert model == "e1000-82545em"


def test_parse_net0_ignores_other_lines():
    raw = "cores: 4\nmemory: 8192\nnet0: vmxnet3,bridge=vmbr3,firewall=0\nsmbios1: uuid=...\n"
    bridge, model = _parse_net0(raw)
    assert bridge == "vmbr3"


def test_parse_net0_no_net0_line():
    raw = "cores: 4\nmemory: 8192\n"
    bridge, model = _parse_net0(raw)
    assert bridge == "vmbr0"
    assert model == "vmxnet3"


# ── build_clone_plan ─────────────────────────────────────────────────


def test_build_clone_plan_step_count_with_apple_services():
    steps = build_clone_plan(900, 901, apple_services=True)
    # clone + smbios + vmgenid + mac = 4 steps
    assert len(steps) == 4


def test_build_clone_plan_step_count_without_apple_services():
    steps = build_clone_plan(900, 901, apple_services=False)
    # clone + smbios = 2 steps
    assert len(steps) == 2


def test_build_clone_plan_first_step_is_qm_clone():
    steps = build_clone_plan(900, 901)
    clone = steps[0]
    assert clone.argv[:3] == ["qm", "clone", "900"]
    assert "901" in clone.argv
    assert "--full" in clone.argv


def test_build_clone_plan_includes_name_when_provided():
    steps = build_clone_plan(900, 901, new_name="my-clone")
    clone = steps[0]
    assert "--name" in clone.argv
    assert "my-clone" in clone.argv


def test_build_clone_plan_no_name_flag_when_omitted():
    steps = build_clone_plan(900, 901, new_name=None)
    clone = steps[0]
    assert "--name" not in clone.argv


def test_build_clone_plan_smbios_step_targets_dst():
    steps = build_clone_plan(900, 901)
    smbios_step = steps[1]
    assert "qm" in smbios_step.argv
    assert "set" in smbios_step.argv
    assert "901" in smbios_step.argv
    assert "--smbios1" in smbios_step.argv


def test_build_clone_plan_smbios_contains_uuid_and_base64():
    steps = build_clone_plan(900, 901)
    smbios_val = next(
        a for a in steps[1].argv if a.startswith("uuid=")
    )
    assert "base64=1" in smbios_val
    # UUID format
    uuid_part = smbios_val.split(",")[0].split("=", 1)[1]
    assert re.fullmatch(
        r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
        uuid_part,
    )


def test_build_clone_plan_vmgenid_step_targets_dst():
    steps = build_clone_plan(900, 901, apple_services=True)
    vmgenid_step = next(s for s in steps if "--vmgenid" in s.argv)
    assert "901" in vmgenid_step.argv
    # vmgenid must be a valid uppercase UUID
    vmgenid_val = vmgenid_step.argv[vmgenid_step.argv.index("--vmgenid") + 1]
    assert re.fullmatch(
        r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
        vmgenid_val,
    )


def test_build_clone_plan_no_vmgenid_step_without_apple_services():
    steps = build_clone_plan(900, 901, apple_services=False)
    titles = [s.title for s in steps]
    assert not any("vmgenid" in t for t in titles)


def test_build_clone_plan_mac_step_targets_dst():
    steps = build_clone_plan(900, 901, apple_services=True)
    mac_step = next(s for s in steps if "--net0" in s.argv)
    assert "901" in mac_step.argv


def test_build_clone_plan_mac_step_preserves_bridge_from_source():
    config_raw = "net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr5,firewall=0\n"
    steps = build_clone_plan(900, 901, apple_services=True, current_net0=config_raw)
    mac_step = next(s for s in steps if "--net0" in s.argv)
    net0_val = mac_step.argv[-1]
    assert "vmbr5" in net0_val


def test_build_clone_plan_mac_step_generates_fresh_mac():
    config_raw = "net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0\n"
    steps = build_clone_plan(900, 901, apple_services=True, current_net0=config_raw)
    mac_step = next(s for s in steps if "--net0" in s.argv)
    net0_val = mac_step.argv[-1]
    # Old MAC must not appear
    assert "AA:BB:CC:DD:EE:FF" not in net0_val
    # A new MAC in macaddr= form must be present
    assert "macaddr=" in net0_val


def test_build_clone_plan_mac_step_preserves_net_model():
    config_raw = "net0: e1000-82545em=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0\n"
    steps = build_clone_plan(900, 901, apple_services=True, current_net0=config_raw)
    mac_step = next(s for s in steps if "--net0" in s.argv)
    net0_val = mac_step.argv[-1]
    assert net0_val.startswith("e1000-82545em")


def test_build_clone_plan_smbios_differs_across_calls():
    steps_a = build_clone_plan(900, 901, apple_services=False)
    steps_b = build_clone_plan(900, 901, apple_services=False)
    smbios_a = next(a for a in steps_a[1].argv if a.startswith("uuid="))
    smbios_b = next(a for a in steps_b[1].argv if a.startswith("uuid="))
    # Two separate calls must produce distinct identities (UUID will differ)
    assert smbios_a != smbios_b


def test_build_clone_plan_vmgenid_differs_across_calls():
    steps_a = build_clone_plan(900, 901, apple_services=True)
    steps_b = build_clone_plan(900, 901, apple_services=True)
    get_vmgenid = lambda steps: next(
        s.argv[s.argv.index("--vmgenid") + 1]
        for s in steps if "--vmgenid" in s.argv
    )
    assert get_vmgenid(steps_a) != get_vmgenid(steps_b)


def test_build_clone_plan_clone_step_has_action_risk():
    steps = build_clone_plan(900, 901)
    assert steps[0].risk == "action"


def test_build_clone_plan_smbios_step_has_safe_risk():
    steps = build_clone_plan(900, 901)
    assert steps[1].risk == "safe"
