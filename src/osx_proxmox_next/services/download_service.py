from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..assets import AssetCheck
from ..defaults import DEFAULT_ISO_DIR
from ..domain import VmConfig
from ..downloader import DownloadError, DownloadProgress, download_opencore, download_recovery

log = logging.getLogger(__name__)

__all__ = ["run_download_worker"]


def run_download_worker(
    config: VmConfig,
    missing: list[AssetCheck],
    on_progress: Callable[[str, int], None],
) -> list[str]:
    """Download missing assets and return a list of error strings.

    *on_progress(phase, pct)* is called on the background thread — callers
    that need to update UI must dispatch to the main thread themselves.
    """
    dest_dir = Path(config.iso_dir or DEFAULT_ISO_DIR)
    errors: list[str] = []

    def _progress_cb(p: DownloadProgress) -> None:
        if p.total > 0:
            pct = int(p.downloaded * 100 / p.total)
            on_progress(p.phase, pct)

    for asset in missing:
        if not asset.downloadable:
            continue
        if "OpenCore" in asset.name:
            try:
                download_opencore(config.macos, dest_dir, on_progress=_progress_cb)
            except DownloadError as exc:
                errors.append(f"OpenCore: {exc}")
        elif "recovery" in asset.name.lower() or "installer" in asset.name.lower():  # pragma: no branch
            try:
                download_recovery(config.macos, dest_dir, on_progress=_progress_cb)
            except DownloadError as exc:
                errors.append(f"Recovery: {exc}")

    return errors
