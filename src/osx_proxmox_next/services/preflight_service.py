from __future__ import annotations

import logging
from collections.abc import Callable

from ..preflight import PreflightCheck, run_preflight, has_missing_build_deps, install_missing_packages

log = logging.getLogger(__name__)

__all__ = ["run_preflight_worker"]


def run_preflight_worker(
    on_status: Callable[[str], None] | None = None,
) -> list[PreflightCheck]:
    """Run preflight checks, auto-installing missing build deps if needed.

    *on_status(msg)* is called on the background thread for progress messages.
    Returns the final list of PreflightCheck results.
    """
    checks = run_preflight()
    if has_missing_build_deps(checks):
        def _on_output(msg: str) -> None:
            if on_status:
                on_status(msg)
        ok, pkgs = install_missing_packages(on_output=_on_output)
        if ok and pkgs:
            checks = run_preflight()
    return checks
