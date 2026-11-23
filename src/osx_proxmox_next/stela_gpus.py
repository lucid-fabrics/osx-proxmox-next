"""The host's GPUs the Stela VM can take, read from `lspci`: the optional pass-through choice."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

__all__ = ["HostGpu", "list_host_gpus", "parse_lspci"]

_HEADER = re.compile(r"^(?P<addr>[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2})\.(?P<fn>[0-7]) (?P<what>.+?) \[(?P<cls>03(?:00|02|80))\]: (?P<name>.+)$")
_DRIVER = re.compile(r"^\s+Kernel driver in use: (?P<drv>\S+)")


@dataclass(frozen=True)
class HostGpu:
    slot: str      # 0000:0a:00, with no function: Proxmox passes every function of the card
    name: str
    driver: str    # the host driver now holding it ("" when none); vfio-pci means it is ready to pass

    @property
    def label(self) -> str:
        return f"{self.slot} {self.name}" + (f" ({self.driver})" if self.driver else "")


def parse_lspci(text: str) -> list[HostGpu]:
    """Display controllers from `lspci -Dnnk` output: one entry per card, found by its first function."""
    gpus: dict[str, HostGpu] = {}
    current: str | None = None
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            current = None
            if header.group("fn") == "0":
                current = header.group("addr")
                name = re.sub(r"\s*\[[0-9a-f]{4}:[0-9a-f]{4}\].*$", "", header.group("name")).strip()
                gpus[current] = HostGpu(current, name, "")
            continue
        driver = _DRIVER.match(line)
        if driver and current:
            gpus[current] = HostGpu(current, gpus[current].name, driver.group("drv"))
    return list(gpus.values())


def list_host_gpus() -> list[HostGpu]:
    try:
        out = subprocess.run(["lspci", "-Dnnk"], capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_lspci(out)
