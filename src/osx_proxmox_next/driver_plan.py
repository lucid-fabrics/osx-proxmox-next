"""The plan of one driver update: what the Mac has, what it would get, why it may be refused, and every line the
CLI and the TUI show. Nothing here changes the Mac."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import driver_engine as engine, driver_oc, driver_snapshot, host_setup, igpu_engine
from .driver_targets import Choice, Target, detect_kind, resolve
from .gpu import parse_config
from .igpu_attach import TAHOE_NOTE, UNSUPPORTED_MACOS, UNSUPPORTED_NOTE
from .infrastructure import ProxmoxAdapter
from .nullmoth import detect_macos
from .services import fetch_vm_info, get_proxmox_adapter

__all__ = ["DriverPlan", "card_setup_waiting", "commands", "installed_versions", "prepare"]


@dataclass
class DriverPlan:
    vmid: int
    name: str = ""
    kind: str = ""
    macos: str = ""
    running: bool = False
    installed: dict[str, str] = field(default_factory=dict)
    installed_known: bool = False
    target: Target | None = None
    verdict: str = ""                 # unknown, update or current
    reinstall: bool = False
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    snapshot: str = ""                # the name the driver snapshot will have
    snapshot_ok: bool = True
    snapshot_reason: str = ""
    older_snapshots: list[str] = field(default_factory=list)
    interrupted: bool = False         # an update was cut off and nobody runs it
    cfg: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def nothing_to_do(self) -> bool:
        """Up to date and the user did not ask to reinstall."""
        return self.ok and self.verdict == "current" and not self.reinstall

    @property
    def needs_no_snapshot_phrase(self) -> bool:
        return not self.snapshot_ok

    @property
    def version_label(self) -> str:
        return ", ".join(self.target.versions().values()) if self.target else ""

    def judged(self) -> dict:
        return engine.judge(self.installed, self.target.versions() if self.target else {})

    def lines(self) -> list[str]:
        """The plan in plain words: the refusals, what changes, the steps, the facts."""
        out = ["Refused: " + r for r in self.refusals]
        if not self.ok or self.target is None:
            return out
        label = engine.CARD_NAME[self.kind]
        out.append(f"Update the {label} driver of {self.name or 'VM %d' % self.vmid} (VM {self.vmid})")
        out += ["  " + l for l in engine.change_lines(self.kind, self.installed, self.target.versions())]
        if self.nothing_to_do:
            return out + [engine.UP_TO_DATE]
        out.append("Steps:")
        out += [f"  {n}. {t}" for n, (_i, t) in enumerate(engine.step_titles(self.kind, self.step_name()), 1)]
        out += engine.before_facts(self.kind, self.snapshot, self.snapshot_ok, self.older_snapshots)
        out += ["Warning: " + w for w in self.warnings]
        return out

    def step_name(self) -> str:
        """What the stage step names: `NullMoth v2.9`, `WhateverXe 1.2`; the AMD update has no stage step."""
        if self.target is None or self.kind == "amd":
            return "the driver"
        name, comp = next(iter(self.target.components.items()))
        return f"{name} {comp['version']}"


def commands(plan: DriverPlan) -> list[str]:
    """The Proxmox commands of an update, written out (the `x` view of "See every change"). The VM ID is a number and
    the snapshot name is made by this tool; nothing else is spliced in."""
    v = str(plan.vmid)
    out = [f"qm shutdown {v} --timeout 120", f"qm snapshot {v} {plan.snapshot} --description "
           f"\"{engine.snapshot_description(plan.version_label)}\""]
    if plan.kind != "amd":
        out += [f"qm set {v} --delete hostpci0", f"qm set {v} --vga std", "(recovery boots, the payload disk stages the driver)",
                f"qm set {v} --hostpci0 <as it was>  --vga <as it was>  --args <as it was>"]
    else:
        out += ["(the OpenCore disk is mounted: Lilu.kext and WhateverGreen.kext are replaced)"]
    return out + [f"qm start {v}", f"qm rollback {v} {plan.snapshot}   (u, driver-undo)"]


def card_setup_waiting(vmid: int) -> bool:
    """A graphics card setup for this Mac that has not finished (it waits for the restart or the first boot question)."""
    try:
        state = host_setup.load_state()
    except Exception:  # noqa: BLE001 - an unreadable state is not a reason to refuse
        return False
    return bool(state) and state.get("vm") == vmid and bool(state.get("gpu")) and not state.get("finished")


def cfg_refusals(vmid: int, cfg: dict[str, str], base: str | None = None, resuming: bool = False) -> list[str]:
    out: list[str] = []
    if "isa-applesmc" not in cfg.get("args", ""):
        out.append(engine.REFUSE_NOT_MAC % vmid)
    if igpu_engine.recovery_bus(cfg) and not resuming:   # a cut-off run may have left its recovery disk
        out.append(engine.REFUSE_INSTALLING)
    if card_setup_waiting(vmid):
        out.append(engine.REFUSE_CARD_SETUP)
    if engine.lock_holder(vmid, base):
        out.append(engine.REFUSE_LOCKED)
    return out


def installed_versions(pve: ProxmoxAdapter, vmid: int, kind: str, running: bool) -> tuple[dict[str, str], bool]:
    """(versions, known). The record first; an AMD Mac without one is read from its OpenCore disk (only while it is
    stopped: the disk is not mounted under a running Mac)."""
    have = engine.installed_versions(engine.read_record(vmid))
    if have:
        return {n: v for n, v in have.items() if n in engine.COMPONENTS[kind] or n == "AMFIPass"}, True
    if kind == "amd" and not running:
        try:
            found = driver_oc.read_versions(pve, vmid, list(engine.COMPONENTS["amd"]))
        except driver_oc.OcError:
            return {}, False
        found = {n: v for n, v in found.items() if v}
        return found, bool(found)
    return {}, False


def prepare(vmid: int, choice: Choice | None = None, pve: ProxmoxAdapter | None = None, reinstall: bool = False,
            get: Callable | None = None, base: str | None = None, resume: dict | None = None) -> DriverPlan:
    """Work out an update of *vmid* to *choice* (the latest when empty). Reads Proxmox and GitHub, changes nothing."""
    pve = pve or get_proxmox_adapter()
    plan = DriverPlan(vmid, reinstall=reinstall)
    info = fetch_vm_info(vmid, adapter=pve)
    if info is None:
        plan.refusals.append(f"VM {vmid} not found.")
        return plan
    plan.cfg = parse_config(info.config_raw)
    plan.name, plan.running = info.name, info.status == "running"
    plan.macos = (choice.macos if choice and choice.macos else detect_macos(plan.cfg)) or ""
    plan.refusals += cfg_refusals(vmid, plan.cfg, base, _pending(vmid, base, resume) is not None)
    if not _find_kind(plan, vmid, base, resume):
        return plan
    _kind_checks(plan)
    if not plan.ok:
        return plan
    try:
        plan.target = resolve(plan.kind, choice or Choice(), get)
    except igpu_engine.ReleaseError as exc:
        plan.refusals.append(str(exc))
        return plan
    plan.installed, plan.installed_known = installed_versions(pve, vmid, plan.kind, plan.running)
    plan.verdict = plan.judged()["state"]
    plan.snapshot = engine.snapshot_name()
    plan.snapshot_ok, plan.snapshot_reason = driver_snapshot.check_support(pve, plan.cfg)
    plan.older_snapshots = driver_snapshot.list_tool(pve, vmid)
    return plan


def _pending(vmid: int, base: str | None, resume: dict | None) -> dict | None:
    """The run that is going or was cut off (not one that waits for the first boot question), else None."""
    state = resume or engine.read_update(vmid, base)
    return state if state and state.get("phase") != "done" else None


def _find_kind(plan: DriverPlan, vmid: int, base: str | None, resume: dict | None) -> bool:
    """Which kind of card the plan is for. False, with the refusal added, when it cannot be told."""
    state = _pending(vmid, base, resume)
    plan.interrupted = bool(resume) or bool(engine.unfinished(vmid, base))
    if state:     # a run that was cut off or is running knows its kind: the card may be off the Mac right now
        plan.kind = state["kind"]
        return True
    found = detect_kind(vmid, plan.cfg)
    plan.kind = found.kind
    if found.conflict:
        plan.refusals.append(engine.REFUSE_KIND_CHANGED)
    elif not plan.kind:
        plan.refusals.append(engine.REFUSE_NO_CARD if found.readable else engine.REFUSE_UNKNOWN_KIND)
    return bool(plan.kind) and not found.conflict


def _kind_checks(plan: DriverPlan) -> None:
    if plan.kind == "intel":
        if plan.macos in UNSUPPORTED_MACOS:
            plan.refusals.append(UNSUPPORTED_NOTE)
        elif plan.macos == "tahoe":
            plan.warnings.append(TAHOE_NOTE)
    if plan.kind in ("nvidia", "intel") and not plan.macos:
        plan.refusals.append("Cannot tell which macOS this Mac runs (its description or name should say). "
                             "Pass --macos (ventura, sonoma, sequoia or tahoe).")
