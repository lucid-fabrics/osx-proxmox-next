"""The rows of the New Mac form that do not depend on the screen: version list, Custom size and More."""

from __future__ import annotations

from collections.abc import Iterator

from textual.widget import Widget

from ..domain import SUPPORTED_MACOS
from ..models.wizard_state import NewMacState
from ..services.host_facts import HostFacts
from ..widgets.rows import ActionRow, ChoiceRow, FieldRow, Option

__all__ = ["VERSION_ORDER", "MORE_ROWS", "version_options", "short_label", "custom_rows", "more_rows"]

# newest first
VERSION_ORDER = ("tahoe", "sequoia", "sonoma", "ventura", "monterey")

PENRYN_HINT_DETECTED = ("Older Intel CPU detected (pre-Skylake). Penryn mode improves macOS install "
                        "stability on this hardware.")
PENRYN_HINT_OTHER = "Only for older Intel CPUs. Xeon chips use the host CPU."
E1000_HINT_DETECTED = ("Xeon or older Intel detected. e1000 needs no kext and avoids slow recovery downloads.")
E1000_HINT_OTHER = "vmxnet3 is the default and is the faster of the two."

MORE_ROWS = ("vmid", "bridge", "vlan", "iso_dir", "installer_path", "existing_uuid", "penryn", "nic",
             "vmgenid", "mac", "force")


def short_label(key: str) -> str:
    return SUPPORTED_MACOS[key]["label"].replace("macOS ", "")


def version_options() -> list[Option]:
    return [Option(key, short_label(key)) for key in VERSION_ORDER if key in SUPPORTED_MACOS]


def _on_off(label: str, value: bool, *, id: str, hint: str = "") -> ChoiceRow:
    row = ChoiceRow(label, [Option("off", "Off"), Option("on", "On")], "on" if value else "off", id=id)
    row.hint = hint
    return row


def custom_rows(st: NewMacState) -> Iterator[Widget]:
    """The three fields Custom reveals under Size."""
    yield FieldRow(" Cores", st.custom_cores, id="r-cores", max_width=8)
    yield FieldRow(" Memory", st.custom_memory_gb, id="r-memory", suffix="GB", max_width=8)
    yield FieldRow(" Disk", st.custom_disk_gb, id="r-disk", suffix="GB", max_width=8)


def more_rows(st: NewMacState, facts: HostFacts) -> Iterator[Widget]:
    """Everything under More: the fields most people never touch."""
    yield FieldRow(" VM ID", st.vmid, id="r-vmid", max_width=10)
    yield FieldRow(" Bridge", st.bridge, id="r-bridge", max_width=12)
    yield FieldRow(" VLAN", st.vlan, id="r-vlan", placeholder="untagged", max_width=12)
    yield FieldRow(" ISO storage", st.iso_dir, id="r-iso")
    yield FieldRow(" Installer", st.installer_path, id="r-installer",
                   placeholder="optional path to an installer")
    yield FieldRow(" UUID", st.existing_uuid, id="r-uuid", placeholder="keep an existing UUID",
                   max_width=40)
    yield _on_off(" CPU model", st.use_penryn, id="r-penryn",
                  hint=PENRYN_HINT_DETECTED if facts.cpu.needs_penryn else PENRYN_HINT_OTHER)
    yield ChoiceRow(" NIC model", [Option("vmxnet3", "vmxnet3"), Option("e1000", "e1000")],
                    "e1000" if st.e1000 else "vmxnet3", id="r-nic")
    yield FieldRow(" vmgenid", st.vmgenid, id="r-vmgenid", placeholder="made for you",
                   max_width=40)
    yield FieldRow(" MAC", st.mac, id="r-mac", placeholder="made for you", max_width=20)
    yield ActionRow(" Identity", id="r-identity")
    yield _on_off(" Download", st.force_download, id="r-force",
                  hint="Off uses what is already here. On fetches the installer files again.")
