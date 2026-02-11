from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .infrastructure import ProxmoxAdapter
from .planner import PlanStep


@dataclass
class StepResult:
    title: str
    command: str
    ok: bool
    returncode: int
    output: str


@dataclass
class ApplyResult:
    ok: bool
    results: list[StepResult]
    log_path: Path


StepCallback = Callable[[int, int, PlanStep, Optional[StepResult]], None]


def apply_plan(
    steps: list[PlanStep],
    execute: bool = False,
    adapter: ProxmoxAdapter | None = None,
    on_step: StepCallback | None = None,
) -> ApplyResult:
    out_dir = Path.cwd() / "generated" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = out_dir / f"apply-{ts}.log"

    runtime = adapter or ProxmoxAdapter()
    results: list[StepResult] = []
    total = len(steps)

    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# apply_plan execute={execute}\n")
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, total, step, None)

            if not execute:
                line = f"[DRY-RUN] {step.title}: {step.command}\n"
                handle.write(line)
                result = StepResult(step.title, step.command, True, 0, line.strip())
                results.append(result)
                if on_step:  # pragma: no branch
                    on_step(idx, total, step, result)
                continue

            cmd_result = runtime.run(step.argv)
            handle.write(f"## {step.title}\n$ {step.command}\n{cmd_result.output}\n")
            result = StepResult(
                title=step.title,
                command=step.command,
                ok=cmd_result.ok,
                returncode=cmd_result.returncode,
                output=cmd_result.output,
            )
            results.append(result)
            if on_step:
                on_step(idx, total, step, result)
            if not cmd_result.ok:
                return ApplyResult(ok=False, results=results, log_path=log_path)

    return ApplyResult(ok=True, results=results, log_path=log_path)
