"""`igpu-report <vmid>`: one tar.gz with the latest crash snapshots, versions and config of an iGPU VM.

The work is done by data/gpu/igpu_diag.py, the same file the power watchdog uses on the host and the bash
installer embeds verbatim. Nothing is posted anywhere: the user attaches the file to a WhateverXe issue.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from . import igpu_engine

__all__ = ["load_diag", "report", "versions_lines"]


def load_diag() -> ModuleType:
    path = igpu_engine.data_dir() / "igpu_diag.py"
    spec = importlib.util.spec_from_file_location("igpu_diag", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(vmid: int, out_dir: Path | None = None) -> tuple[Path, str]:
    """(bundle path, plain summary). Raises OSError when the bundle cannot be written."""
    path, summary = load_diag().build_report(vmid, str(out_dir) if out_dir else None)
    return Path(path), summary


def versions_lines(vmid: int) -> list[str]:
    """What is installed in the VM, from the host's state file (empty when nothing was recorded)."""
    return igpu_engine.describe_state(igpu_engine.read_state(vmid))
