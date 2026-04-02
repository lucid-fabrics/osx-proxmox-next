from __future__ import annotations

import logging
from collections.abc import Callable

from ..executor import ApplyResult, StepResult, apply_plan
from ..domain import EditChanges, PlanStep

log = logging.getLogger(__name__)

__all__ = ["run_edit_worker"]


def run_edit_worker(
    vmid: int,
    changes: EditChanges,
    start_after: bool = False,
    on_step: Callable[[int, int, PlanStep, StepResult | None], None] | None = None,
) -> ApplyResult:
    """Execute the edit plan for *vmid* with the given *changes*.

    Stops the VM, applies changes, and optionally restarts it.
    *on_step* is called from the calling thread after each step.
    """
    from ..planner import build_edit_plan  # lazy — avoids planner ↔ services circular import
    steps = build_edit_plan(vmid, changes, start_after=start_after)
    return apply_plan(steps, execute=True, on_step=on_step)
