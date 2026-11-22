"""What every driver update shares: the run environment, the step list that survives a closed terminal, the lock,
stopping the Mac, the safety snapshot and taking the card off and putting it back."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import driver_engine as engine, driver_snapshot, nullmoth_engine
from .domain import MAX_VMID, MIN_VMID
from .driver_plan import DriverPlan
from .igpu_finish import wait_stopped
from .infrastructure import ProxmoxAdapter
from .planner import POST_INSTALL_BOOT_ORDER
from .unattended import QmConsole

__all__ = ["DriverError", "RunEnv", "RunResult", "Updater", "stop_mac"]

SHUTDOWN_WAIT = 120          # seconds macOS gets to shut down before the Mac is stopped by force
CARD_KEYS = (("hostpci0", "--hostpci0"), ("vga", "--vga"), ("args", "--args"))


class DriverError(Exception):
    """An update step did not reach its expected state."""


@dataclass
class RunEnv:
    """How a run reaches Proxmox, GitHub and the console, and how it paces itself. Tests replace the hooks."""
    pve: ProxmoxAdapter
    console: QmConsole | None = None
    iso_dir: Path | None = None
    work_dir: Path | None = None
    on_step: Callable[[str, str], None] = lambda _sid, _state: None
    on_event: Callable[[str], None] = lambda _m: None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    no_snapshot: bool = False                      # the user typed the "no snapshot" phrase
    base: str | None = None                        # state folder (tests)
    get: Callable = nullmoth_engine.github_get
    read: Callable = nullmoth_engine._read_url
    fetch: Callable = nullmoth_engine.download
    find_ip: Callable[[str], str] = nullmoth_engine.find_guest_ip
    probe: Callable[[str, int], bool] = nullmoth_engine.port_open
    accel: Callable | None = None                  # Intel: the host-side check that macOS uses the iGPU


@dataclass
class RunResult:
    ok: bool = False
    error: str = ""
    notes: list[str] = field(default_factory=list)
    snapshot: str = ""
    installed: dict[str, dict] = field(default_factory=dict)   # components to record
    detail: str = ""                                           # what was verified, in plain words


def stop_mac(env: RunEnv, vmid: int) -> None:
    """Stop the Mac the way the TUI does: ask macOS to shut down, and only when it does not within two minutes (the
    plan said so up front) stop it by force."""
    pve = env.pve
    if "stopped" in pve.qm("status", str(vmid)).output:
        return
    env.on_event("Asking the Mac to shut down")
    pve.qm("shutdown", str(vmid), "--timeout", str(SHUTDOWN_WAIT))
    if wait_stopped(pve, vmid, 30, env.clock, env.sleep):
        return
    env.on_event(f"macOS did not shut down within {SHUTDOWN_WAIT // 60} minutes. Stopping the Mac by force")
    pve.qm("stop", str(vmid))
    if not wait_stopped(pve, vmid, 120, env.clock, env.sleep):
        raise DriverError(f"VM {vmid} would not stop.")


class Updater:
    """One update of one Mac. Its state lives in <vmid>.update.json, so a reopened terminal sees the same steps."""

    def __init__(self, plan: DriverPlan, env: RunEnv, resume: dict | None = None) -> None:
        if not MIN_VMID <= plan.vmid <= MAX_VMID:
            raise DriverError("That VM ID is out of range.")
        self.plan, self.env, self.pve, self.vmid = plan, env, env.pve, plan.vmid
        self.result = RunResult()
        self.resumed = bool(resume)
        self.state = dict(resume) if resume else self._fresh()
        self.state.update(phase="running", error="", pid=os.getpid())

    def _fresh(self) -> dict:
        plan = self.plan
        steps = [{"id": sid, "title": title, "done": done, "state": "pending"}
                 for (sid, title), (_s, done) in zip(engine.step_titles(plan.kind, plan.step_name()),
                                                     engine.step_titles(plan.kind, plan.step_name(), done=True))]
        return {"kind": plan.kind, "name": plan.name, "label": plan.version_label, "steps": steps, "snapshot": "",
                "original": None, "events": [], "choice": self._choice(),
                "previous": [{"name": n, "version": v} for n, v in plan.installed.items()]}

    def _choice(self) -> dict:
        target = self.plan.target
        tags = {n: c["tag"] for n, c in (target.components if target else {}).items()}
        return {"NullMoth": tags.get("NullMoth", ""), "WhateverXe": tags.get("WhateverXe", ""),
                "WhateverXe Graphics": tags.get("WhateverXe Graphics", "")}

    # ── progress ─────────────────────────────────────────────────────

    def persist(self) -> None:
        engine.write_update(self.vmid, self.state, self.env.base)

    def say(self, message: str) -> None:
        self.state["events"] = (self.state.get("events", []) + [time.strftime("%H:%M:%S ") + message])[-40:]
        self.persist()
        self.env.on_event(message)

    def _row(self, sid: str) -> dict:
        return next(r for r in self.state["steps"] if r["id"] == sid)

    def begin(self, sid: str) -> bool:
        """Start a step. False when a resumed run already did it."""
        row = self._row(sid)
        if row["state"] == "done" and self.resumed and sid in ("stop", "snapshot", "takeoff"):
            return False
        row.update(state="active", started=time.time())
        self.persist()
        self.env.on_step(sid, "active")
        return True

    def end(self, sid: str, title: str = "") -> None:
        row = self._row(sid)
        row.update(state="done", ended=time.time())
        if title:
            row["done"] = title
        self.persist()
        self.env.on_step(sid, "done")

    def fail(self, error: str) -> None:
        for row in self.state["steps"]:
            if row["state"] == "active":
                row.update(state="fail", ended=time.time())
                self.env.on_step(row["id"], "fail")
        self.state.update(phase="failed", error=error)
        self.persist()

    # ── shared steps ─────────────────────────────────────────────────

    def stop(self) -> None:
        if self.begin("stop"):
            stop_mac(self.env, self.vmid)
            self.end("stop")

    def snapshot(self) -> None:
        if not self.begin("snapshot"):
            return
        if self.plan.snapshot_ok:
            name = driver_snapshot.create(self.pve, self.vmid, self.plan.version_label, self.plan.snapshot)
            self.state["snapshot"] = name
            self.result.snapshot = name
            self.say(f"Saved the snapshot {name}")
        elif self.env.no_snapshot:
            self.say("Going on without a snapshot, as you typed. Nothing can put this Mac back.")
        else:
            raise DriverError(engine.NO_SNAPSHOT_WARNING)
        self.end("snapshot")

    def take_card_off(self, strip: Callable[[str], str] | None = None) -> None:
        """Remember hostpci0, vga, args and boot as they are, then remove the card so recovery can run on the emulated
        display. *strip* may also drop leftover args. A resumed run keeps what the first run remembered."""
        if not self.begin("takeoff"):
            return
        if not self.state.get("original"):
            cfg = self.plan.cfg
            self.state["original"] = {k: cfg.get(k) for k in ("hostpci0", "vga", "args", "boot")}
            self.persist()
        res = self.pve.qm("set", str(self.vmid), "--delete", "hostpci0")
        if not res.ok and "hostpci0" in self.pve.qm("config", str(self.vmid)).output:
            raise DriverError(f"Could not take the card off VM {self.vmid}: {res.output.strip()[-300:]}")
        self.end("takeoff")

    def put_card_back(self, boot: str = "") -> None:
        """hostpci0, the display and the args go back to what the first step remembered; the boot order to *boot*."""
        if not self.begin("putback"):
            return
        self.restore_card(boot)
        self.end("putback")

    def restore_card(self, boot: str = "") -> None:
        original = self.state.get("original") or {}
        for key, flag in CARD_KEYS:
            value = original.get(key)
            res = (self.pve.qm("set", str(self.vmid), "--delete", key) if value is None
                   else self.pve.qm("set", str(self.vmid), flag, value))
            if not res.ok and value is not None:
                raise DriverError(f"Could not put {key} back on VM {self.vmid}: {res.output.strip()[-300:]}")
        order = boot or original.get("boot") or POST_INSTALL_BOOT_ORDER
        self.pve.qm("set", str(self.vmid), "--boot", order)

    def start(self) -> None:
        res = self.pve.qm("start", str(self.vmid))
        if not res.ok:
            raise DriverError(f"Could not start VM {self.vmid}: {res.output.strip()[-300:]}")
