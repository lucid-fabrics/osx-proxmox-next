"""The VM steps of "See every change" in plain words: one line per step saying what it changes.

The exact commands stay one key away (x in the dialog). Nothing here imports Textual."""

from __future__ import annotations

import re
import shlex
from typing import Any

from .domain import PlanStep

__all__ = ["step_summary", "vm_step_lines"]

MORE = "Press x to see the exact command."
_OC = re.compile(r'"\$OC_MNT/EFI/OC" (\w+)')
_BOOT_ARGS = re.compile(r"GPU_BOOT_ARGS=('[^']*'|\S+)")
_RECORD = re.compile(r"gpu-attach-(\d+)\.json")
_OC_DISK = "Edits OpenCore's config.plist on the Mac's boot disk"
_QM_SET = {
    "--hostpci0": "Hands the card ({value}) to VM {vid} as hostpci0",
    "--vga": "Sets the emulated display of VM {vid} to {value}",
    "--machine": "Sets the machine type of VM {vid} to {value}",
    "--boot": "Sets the boot order of VM {vid} to {value}",
    "--description": "Writes the note \"{value}\" in the description of VM {vid}",
}


def _boot_args(script: str) -> list[str]:
    found = _BOOT_ARGS.search(script)
    return shlex.split(found.group(1)) if found else []


def _oc_line(script: str, title: str) -> str:
    """The OpenCore edit of a script, with its real values when it names them."""
    found = _OC.search(script)
    action = found.group(1) if found else ""
    if action in ("restore", "detach"):
        return "Puts the Mac's OpenCore config.plist back as it was before the card"
    args = _boot_args(script)
    if action == "attach" and args:
        return f"{_OC_DISK}: adds boot argument{'s' if len(args) != 1 else ''} {' '.join(args)}"
    if action == "fadt":
        return f"{_OC_DISK}: sets FadtEnableReset so restarts do not hang"
    if "NullMoth" in title:
        return f"{_OC_DISK}: adds the NullMoth driver files and boot arguments"
    if "WhateverXe" in title:
        return f"{_OC_DISK}: adds WhateverXe, AMFIPass and the iGPU boot arguments"
    return f"{_OC_DISK}: {title[:1].lower() + title[1:]}"


def _qm_line(argv: list[str]) -> str:
    vid, flag, value = argv[2], argv[3] if len(argv) > 3 else "", " ".join(argv[4:])
    if flag == "--delete":
        return f"Removes {value} from VM {vid}"
    if flag == "--vga" and value == "none":
        return f"Turns the emulated display off on VM {vid} (vga none), the card is the display"
    if flag == "--args":
        return f"Changes the extra QEMU arguments of VM {vid}"
    if flag in _QM_SET:
        return _QM_SET[flag].format(vid=vid, value=value)
    return f"Changes {flag.lstrip('-')} of VM {vid}" if flag else ""


def _bash_line(step: PlanStep) -> str:
    script = step.argv[2] if len(step.argv) > 2 else ""
    if "$OC_MNT" in script:
        return _oc_line(script, step.title)
    record = _RECORD.search(script)
    if record and "printf" in script:
        return (f"Saves the current settings of VM {record.group(1)} so undo can put them back "
                f"(/var/lib/osx-proxmox-next/gpu-attach-{record.group(1)}.json)")
    vid = re.search(r"qm status '?(\d+)", script)
    if vid and "status: stopped" in script:
        return f"Checks VM {vid.group(1)} is stopped before touching it"
    if vid and "qm stop" in script:
        return f"Shuts down VM {vid.group(1)} so the card can be attached"
    return ""


def step_summary(step: PlanStep) -> str:
    """One plain line for what *step* changes, real values included. The exact command is behind x."""
    argv = step.argv
    line = ""
    if argv[:2] == ["qm", "set"] and len(argv) > 2:
        line = _qm_line(argv)
    elif argv[:1] == ["bash"]:
        line = _bash_line(step)
    elif argv[:2] == ["rm", "-f"]:
        line = f"Deletes the saved settings file {argv[2]}"
    elif argv[:1] == ["install"] and len(argv) > 2:
        line = f"Copies the option ROM to {argv[-1]}"
    return line or f"Runs one command. {MORE}"


def vm_step_lines(vm_plan: Any, exact: bool) -> list[str]:
    """The numbered steps of an attach plan: the plain line, or the exact command under each title."""
    out = []
    for idx, step in enumerate(vm_plan.steps, start=1):
        out.append(f"  {idx:02d}. {step.title}")
        out.append(f"      {step.command if exact else step_summary(step)}")
    return out
