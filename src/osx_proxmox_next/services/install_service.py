from __future__ import annotations

import logging
from collections.abc import Callable

from ..executor import ApplyResult, StepResult, apply_plan
from ..domain import PlanStep
from ..rollback import RollbackSnapshot, create_snapshot

log = logging.getLogger(__name__)

__all__ = ["run_dry_apply", "run_live_install"]


def run_dry_apply(
    steps: list[PlanStep],
    on_step: Callable[[int, int, PlanStep, StepResult | None], None] | None = None,
) -> ApplyResult:
    """Execute a dry run (execute=False) of the given plan steps.

    *on_step* is called on the background thread after each step.
    """
    return apply_plan(steps, execute=False, on_step=on_step)


def run_live_install(
    vmid: int,
    steps: list[PlanStep],
    on_step: Callable[[int, int, PlanStep, StepResult | None], None] | None = None,
) -> tuple[ApplyResult, RollbackSnapshot | None]:
    """Create a rollback snapshot then execute the live install plan.

    Returns (ApplyResult, snapshot).
    *on_step* is called on the background thread after each step.
    """
    snapshot = create_snapshot(vmid)
    result = apply_plan(steps, execute=True, on_step=on_step)
    return result, snapshot
