"""The plan of one driver update: what the Mac has, what it would get, why it may be refused, and every line the
CLI and the TUI show. Nothing here changes the Mac."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import driver_disks, driver_engine as engine, driver_oc, driver_snapshot, host_setup, igpu_engine
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
    from_record: bool = False         # the installed versions are the record's: the Mac's disk could not be read (it runs)
    undo: str = ""                    # how this tool says "undo" (empty: the key r and osx-next-cli; bash has its own command)
    resume: dict = field(default_factory=dict)   # the state of the stopped run a Finish goes on with

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
        """The plan in plain words: the refusals, what changes, the steps, the facts. A stopped update shows what Finish
        does (no new snapshot, the steps still to do), never the plan of a new update."""
        out = ["Refused: " + r for r in self.refusals]
        if not self.ok or self.target is None:
            return out
        label = engine.CARD_NAME[self.kind]
        who = f"{self.name or 'VM %d' % self.vmid} (VM {self.vmid})"
        finishing = self.finishing
        out.append(f"Finish the {label} driver update of {who}" if finishing else f"Update the {label} driver of {who}")
        out += ["  " + l for l in engine.change_lines(self.kind, self.installed, self.target.versions())]
        if self.nothing_to_do:
            return out + [engine.UP_TO_DATE]
        out.append("Steps:")
        if finishing:
            out += [f"  {n}. {t}" for n, (_i, t) in enumerate(engine.todo_steps(self.kind, self.step_name(), self.running, self.resume), 1)]
            out += engine.finish_facts(self.kind, self.resume.get("snapshot", ""), self.undo)
        else:
            out += [f"  {n}. {t}" for n, (_i, t) in enumerate(engine.plan_steps(self.kind, self.step_name(), self.running), 1)]
            out += engine.before_facts(self.kind, self.snapshot, self.snapshot_ok, self.older_snapshots, self.undo)
        out += ["Warning: " + w for w in self.warnings]
        return out

    @property
    def finishing(self) -> bool:
        """The plan is for the Finish of a stopped run, not for a new update."""
        return self.interrupted and bool(self.resume)

    def step_name(self) -> str:
        """What the stage step names: `NullMoth v2.9`, `WhateverXe 1.2`; the AMD update has no stage step."""
        if self.target is None or self.kind == "amd":
            return "the driver"
        name, comp = next(iter(self.target.components.items()))
        return f"{name} {comp['version']}"


def commands(plan: DriverPlan) -> list[str]:
    """The Proxmox commands of an update, written out (the `x` view of "See every change"). The VM ID is a number and
    the snapshot name is made by this tool; nothing else is spliced in. Only what really happens is listed: the stop only
    when the Mac runs, the recovery and payload disks, the OpenCore kext swap."""
    v = str(plan.vmid)
    out = [f"qm shutdown {v} --timeout 120"] if plan.running else []
    if not plan.finishing:       # Finish saves no new snapshot
        out.append(f"qm snapshot {v} {plan.snapshot} --description \"{engine.snapshot_description(plan.version_label)}\"")
        out += [f"qm delsnapshot {v} {name}   (the previous driver snapshot)" for name in plan.older_snapshots]
    if plan.kind == "amd":
        out.append("(the OpenCore disk is mounted: Lilu.kext and WhateverGreen.kext are replaced)")
    else:
        out += [f"qm set {v} --delete hostpci0",
                f"qm set {v} --sata1 <macOS {plan.macos or 'recovery'} recovery image> --sata2 <payload disk with the driver> "
                "--boot order=sata1;virtio0;ide0",
                f"qm set {v} --vga std",
                "(recovery boots and the payload disk stages the driver inside macOS; both disks are then removed)"]
        out.append("(the OpenCore disk is mounted: WhateverXe.kext and AMFIPass.kext are replaced)" if plan.kind == "intel"
                   else "(the OpenCore disk is mounted: AMFIPass.kext is replaced when it is another version)")
        out.append(f"qm set {v} --hostpci0 <as it was>  --vga <as it was>  --args <as it was>  --boot <as it was>")
    undo_to = (plan.resume.get("snapshot") if plan.finishing else "") or plan.snapshot
    return out + [f"qm start {v}", f"qm rollback {v} {undo_to}   (r, driver-undo)"]


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


DISK_COMPONENTS = {"amd": ("Lilu", "WhateverGreen"), "intel": ("WhateverXe",)}


def installed_versions(pve: ProxmoxAdapter, vmid: int, kind: str, running: bool,
                       cfg: dict[str, str] | None = None, source: dict | None = None) -> tuple[dict[str, str], bool]:
    """(versions, known). What the Mac itself says wins over the record: the kexts on a stopped Mac's OpenCore disk
    (Lilu and WhateverGreen for AMD, WhateverXe for Intel; the disk is not mounted under a running Mac). A record made for
    other disks than the Mac has now (a manual rollback happened since) is not believed, and one that disagrees with the
    OpenCore disk is not either: the versions are then unknown or the disk's. *source*, when given, gets "record" set to True
    when the versions of an Intel or AMD Mac are the record's because the disk was not read (the Mac runs)."""
    record = engine.read_record(vmid)
    have = {n: v for n, v in engine.installed_versions(record).items() if n in engine.COMPONENTS[kind] or n == "AMFIPass"}
    if have and cfg is not None and not driver_disks.record_is_true(pve, vmid, record, cfg):
        have = {}
    found: dict[str, str] = {}
    if kind in DISK_COMPONENTS and not running:
        try:
            found = driver_oc.read_versions(pve, vmid, list(DISK_COMPONENTS[kind]))
        except driver_oc.OcError:
            found = {}
        found = {n: v for n, v in found.items() if v}
        if found:
            if any(n in have and engine.compare(have[n], v) != 0 for n, v in found.items()):
                have = {}       # the disk disagrees: what the record says about the other parts is stale too
            have.update(found)
    if source is not None:
        source["record"] = bool(have) and kind in DISK_COMPONENTS and not found
    return have, bool(have)


def prepare(vmid: int, choice: Choice | None = None, pve: ProxmoxAdapter | None = None, reinstall: bool = False,
            get: Callable | None = None, base: str | None = None, resume: dict | None = None,
            snapshot: str = "") -> DriverPlan:
    """Work out an update of *vmid* to *choice* (the latest when empty). Reads Proxmox and GitHub, changes nothing.
    The snapshot name is made here, once: *snapshot* is the name a screen already showed, which the run then uses."""
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
    where: dict = {}
    plan.installed, plan.installed_known = installed_versions(pve, vmid, plan.kind, plan.running, plan.cfg, where)
    plan.from_record = bool(where.get("record"))
    plan.resume = resume or (engine.unfinished(vmid, base) if plan.interrupted else {})
    plan.verdict = plan.judged()["state"]
    plan.snapshot = snapshot if engine.SNAPSHOT_RE.match(snapshot or "") else engine.snapshot_name()
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
        if plan.macos not in UNSUPPORTED_MACOS:
            plan.warnings.append(engine.INTEL_BOOT_NOTE)
    if plan.kind in ("nvidia", "intel") and not plan.macos:
        plan.refusals.append("Cannot tell which macOS this Mac runs (its description or name should say). "
                             "Pass --macos (ventura, sonoma, sequoia or tahoe).")
