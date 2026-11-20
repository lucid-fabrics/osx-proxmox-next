"""The Build pipeline: one run from "Check this host" to a ready Mac.

A BuildPipeline owns the state the Build screen draws (phases, durations,
log, failure) and does the work on whatever thread calls run(). Nothing on
the host changes before the rehearsal passes. The hands-off install is not
done here: it is a detached process the pipeline starts and the screen then
watches through its log (unattended_service)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..assets import required_assets
from ..domain import PlanStep, VmConfig
from ..executor import StepResult
from ..planner import build_plan
from ..preflight import PreflightCheck, run_preflight
from ..rollback import RollbackSnapshot, rollback_hints
from .destroy_service import run_destroy_worker
from .detection_service import fetch_vm_info
from .download_service import run_download_worker
from .install_service import run_dry_apply, run_live_install
from .unattended_service import DriverState, driver_log_path, spawn_unattended_driver

log = logging.getLogger(__name__)

__all__ = ["PHASE_IDS", "PhaseState", "PipelineDeps", "BuildPipeline", "phase_title"]

PHASE_IDS = ("check", "download", "rehearse", "create", "install", "ready")

PENDING, ACTIVE, OK, FAILED, SKIPPED = "pending", "active", "ok", "failed", "skipped"

# How long a fresh driver may be missing from /proc before it counts as gone.
DRIVER_GRACE_SECONDS = 15


@dataclass
class PhaseState:
    id: str
    status: str = PENDING
    started: float = 0.0
    ended: float = 0.0
    note: str = ""

    def duration(self, now: float) -> float:
        if not self.started:
            return 0.0
        return (self.ended or now) - self.started


def phase_title(phase_id: str, status: str, vmid: int = 0, steps: int = 0) -> str:
    """The name of a phase in the timeline: active or pending form while it runs, past form once done."""
    done = status in (OK, SKIPPED)
    count = f"all {steps} steps" if steps else "all steps"
    names = {
        "check": ("Check this host", "Checked this host"),
        "download": ("Download macOS", "Downloaded macOS"),
        "rehearse": (f"Rehearse {count}", f"Rehearsed {count}"),
        "create": (f"Create VM {vmid}", f"Created VM {vmid}"),
        "install": ("Install macOS", "Installed macOS"),
        "ready": ("Ready", "Ready"),
    }
    active, past = names[phase_id]
    if phase_id == "install" and status == ACTIVE:
        return "Installing macOS"
    return past if done else active


@dataclass
class PipelineDeps:
    """Everything the pipeline touches outside itself, so tests can swap it."""
    preflight: Callable[[], list[PreflightCheck]] = run_preflight
    assets: Callable[[VmConfig], list] = required_assets
    download: Callable[..., list[str]] = run_download_worker
    plan: Callable[[VmConfig], list[PlanStep]] = build_plan
    dry_run: Callable[..., object] = run_dry_apply
    live: Callable[..., tuple] = run_live_install
    vm_exists: Callable[[int], bool] = lambda vmid: fetch_vm_info(vmid) is not None
    destroy: Callable[..., tuple] = run_destroy_worker
    spawn: Callable[[int, int], str] = spawn_unattended_driver
    clock: Callable[[], float] = time.time


class BuildPipeline:
    def __init__(self, config: VmConfig | None, unattended: bool, force_download: bool = False,
                 deps: PipelineDeps | None = None, vmid: int = 0) -> None:
        self.config = config
        self.vmid = config.vmid if config else vmid
        self.unattended = unattended
        self.force_download = force_download
        self.deps = deps or PipelineDeps()
        self.phases = [PhaseState(pid) for pid in PHASE_IDS]
        if not unattended:
            self._phase("install").status = SKIPPED
        self.on_change: Callable[[], None] = lambda: None
        self.log_lines: list[str] = []
        self.plan_steps: list[PlanStep] = []
        self.checks: list[PreflightCheck] = []
        self.failed: str = ""          # id of the failed phase, "" when none
        self.failure: str = ""         # why it failed
        self.rollback: list[str] = []  # hints shown after a failed Create VM
        self.live_log: Path | None = None
        self.running = False           # an in-process phase is running (a quit must be confirmed)
        self.started_at = 0.0
        self.download_text = ""
        self.driver_log = ""
        self.driver = DriverState()
        self._created_by_us = False
        self._driver_started = 0.0

    # ── state helpers ────────────────────────────────────────────────

    def _phase(self, phase_id: str) -> PhaseState:
        return self.phases[PHASE_IDS.index(phase_id)]

    def phase(self, phase_id: str) -> PhaseState:
        return self._phase(phase_id)

    @property
    def host_failed(self) -> bool:
        return self.failed == "check"

    @property
    def done(self) -> bool:
        return self._phase("ready").status == OK

    @property
    def can_retry(self) -> bool:
        return bool(self.failed)

    @property
    def safe_to_close(self) -> bool:
        """The detached driver owns the install: quitting no longer stops anything."""
        return self._phase("install").status == ACTIVE and bool(self.driver_log)

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.deps.clock()
        return max(0.0, end - self.started_at)

    def _say(self, line: str) -> None:
        self.log_lines.append(line)
        self.on_change()

    def _begin(self, phase_id: str) -> PhaseState:
        phase = self._phase(phase_id)
        phase.status, phase.started, phase.ended, phase.note = ACTIVE, self.deps.clock(), 0.0, ""
        self.on_change()
        return phase

    def _end(self, phase_id: str, status: str = OK, note: str = "") -> None:
        phase = self._phase(phase_id)
        phase.status, phase.ended = status, self.deps.clock()
        if note:
            phase.note = note
        self.on_change()

    def _fail(self, phase_id: str, reason: str) -> None:
        self.failed, self.failure = phase_id, reason
        self._say(f"FAILED: {reason}")
        self._end(phase_id, FAILED, reason)
        self.running = False

    # ── the run ──────────────────────────────────────────────────────

    def run(self, start_at: str = "check") -> None:
        """Run the phases from *start_at* until one fails or the in-process work ends."""
        if self.config is None:
            raise RuntimeError("this pipeline was rebuilt from a log and cannot run")
        self.running = True
        self.failed, self.failure, self.rollback = "", "", []
        if not self.started_at:
            self.started_at = self.deps.clock()
        order = list(PHASE_IDS)
        steps = {
            "check": self._check,
            "download": self._download,
            "rehearse": self._rehearse,
            "create": self._create,
            "install": self._install,
        }
        for phase_id in order[order.index(start_at):]:
            if phase_id == "ready":
                break
            if phase_id == "install" and not self.unattended:
                continue
            if not steps[phase_id]():
                return
            if phase_id == "install":
                self.running = False
                return  # the detached driver owns the rest; apply_driver_state() finishes it
        self._finish_manual()

    def _finish_manual(self) -> None:
        self._end("ready")
        self.running = False
        self._say("The VM is created. Finish the install in the Proxmox console of this VM.")

    def _check(self) -> bool:
        self._begin("check")
        self._say("Checking this host")
        self.checks = self.deps.preflight()
        bad = [c for c in self.checks if not c.ok]
        if bad:
            self._fail("check", f"{len(bad)} host check{'s' if len(bad) != 1 else ''} failed: "
                       + ", ".join(c.name for c in bad))
            return False
        self._end("check")
        return True

    def _download(self) -> bool:
        assert self.config is not None
        self._begin("download")
        assets = self.deps.assets(self.config)
        missing = [a for a in assets if not a.ok]
        stale = [a for a in assets if a.ok and a.stale and a.downloadable]
        todo = missing + stale
        if self.force_download and not todo:
            todo = [a for a in assets if a.downloadable]
        if not todo:
            self._end("download", OK, "already here")
            self._say("Assets: all present")
            return self._replan()
        if any(not a.downloadable for a in missing):
            names = ", ".join(a.name for a in missing if not a.downloadable)
            self._fail("download", f"Missing assets: {names}. Provide the path in More, under installer path.")
            return False

        def on_progress(phase: str, pct: int) -> None:
            self.download_text = f"Finalizing {phase}..." if pct >= 100 else f"Downloading {phase}... {pct}%"
            self.on_change()

        self._say("Downloading: " + ", ".join(a.name for a in todo))
        errors = self.deps.download(self.config, todo, on_progress=on_progress,
                                    force_opencore=self.force_download)
        self.download_text = ""
        if errors:
            self._fail("download", "Download errors: " + "; ".join(errors))
            return False
        self._end("download")
        return self._replan()

    def _replan(self) -> bool:
        """The plan depends on what the download found, so it is built after it."""
        assert self.config is not None
        try:
            self.plan_steps = self.deps.plan(self.config)
        except ValueError as exc:
            self._fail("download", f"The plan could not be built: {exc}")
            return False
        return True

    def _rehearse(self) -> bool:
        self._begin("rehearse")
        total = len(self.plan_steps)
        self._say(f"Rehearsing all {total} steps (nothing changes on the host)")

        def on_step(idx: int, total_steps: int, step: PlanStep, result: StepResult | None) -> None:
            self._say(self._step_line(idx, total_steps, step.title, result))

        result = self.deps.dry_run(self.plan_steps, on_step=on_step)
        if not result.ok:  # type: ignore[attr-defined]
            self._fail("rehearse", f"The rehearsal failed. Log: {result.log_path}")  # type: ignore[attr-defined]
            return False
        self._end("rehearse")
        return True

    def _create(self) -> bool:
        assert self.config is not None
        vmid = self.config.vmid
        if self.deps.vm_exists(vmid):
            if self._created_by_us:
                self._say(f"Removing the half-built VM {vmid} before trying again")
                self.deps.destroy(vmid, purge=True)
            else:
                self._begin("create")
                self._fail("create", f"VM {vmid} already exists. Nothing was changed.")
                return False
        self._begin("create")
        self._created_by_us = True
        self._say(f"Creating VM {vmid}")

        def on_step(idx: int, total_steps: int, step: PlanStep, result: StepResult | None) -> None:
            self._say(self._step_line(idx, total_steps, step.title, result))

        result, snapshot = self.deps.live(vmid, self.plan_steps, on_step=on_step)
        self.live_log = result.log_path
        if not result.ok:
            self.rollback = self._hints(snapshot)
            self._fail("create", f"Creating the VM failed. Log: {result.log_path}")
            return False
        self._end("create")
        return True

    def _install(self) -> bool:
        assert self.config is not None
        self._begin("install")
        self._driver_started = self.deps.clock()
        self.driver_log = self.deps.spawn(self.config.vmid, self.config.disk_gb)
        self._say(f"Hands-off install started. Log: {self.driver_log}")
        return True

    @staticmethod
    def _hints(snapshot: RollbackSnapshot | None) -> list[str]:
        return rollback_hints(snapshot) if snapshot else []

    @staticmethod
    def _step_line(idx: int, total: int, title: str, result: StepResult | None) -> str:
        if result is None:
            return f"Running {idx}/{total}: {title}"
        return f"{'OK' if result.ok else 'FAIL'} {idx}/{total}: {title} (rc={result.returncode})"

    # ── the detached driver ──────────────────────────────────────────

    def apply_driver_state(self, state: DriverState) -> None:
        """Fold the driver's log into the Install phase and, when it is done, the Ready phase."""
        self.driver = state
        if self._phase("install").status != ACTIVE:
            return
        if state.finished:
            self._end("install")
            self._end("ready")
            self._say("The install reached Setup Assistant.")
        elif state.error:
            self._fail("install", state.error)
        elif not state.alive and self.deps.clock() - self._driver_started > DRIVER_GRACE_SECONDS:
            last = state.events[-1][1] if state.events else "no output yet"
            self._fail("install", f"The install driver stopped. Last line: {last}")
        else:
            self.on_change()

    @classmethod
    def from_driver(cls, vmid: int, state: DriverState, deps: PipelineDeps | None = None) -> "BuildPipeline":
        """Rebuild the view of a hands-off install that is already running (or ended) from its log."""
        pipe = cls(None, True, deps=deps, vmid=vmid)
        pipe.started_at = state.started_at
        pipe.driver_log = driver_log_path(vmid)
        for phase_id in ("check", "download", "rehearse", "create"):
            pipe._phase(phase_id).status = OK
        pipe._phase("install").status = ACTIVE
        pipe._phase("install").started = state.started_at  # the clock of the install keeps running across reopens
        pipe._driver_started = 0.0
        pipe.apply_driver_state(state)
        return pipe
