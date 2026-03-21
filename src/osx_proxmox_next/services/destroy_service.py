from __future__ import annotations

import logging
from collections.abc import Callable

from ..executor import ApplyResult, StepResult, apply_plan
from ..domain import PlanStep
from ..planner import build_destroy_plan
from ..rollback import RollbackSnapshot, create_snapshot

log = logging.getLogger(__name__)

__all__ = ["run_destroy_worker"]


def run_destroy_worker(
    vmid: int,
    purge: bool = False,
    on_step: Callable[[int, int, PlanStep, StepResult | None], None] | None = None,
) -> tuple[ApplyResult, RollbackSnapshot | None]:
    """Create a rollback snapshot then execute the destroy plan.

    Returns (ApplyResult, snapshot).
    *on_step* is called on the background thread after each step.
    """
    snapshot = create_snapshot(vmid)
    steps = build_destroy_plan(vmid, purge=purge)
    result = apply_plan(steps, execute=True, on_step=on_step)
    return result, snapshot
