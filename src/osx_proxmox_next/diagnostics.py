from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tarfile

from .preflight import run_preflight


@dataclass
class HealthStatus:
    score: int
    total: int
    summary: str


def build_health_status() -> HealthStatus:
    checks = run_preflight()
    ok = sum(1 for c in checks if c.ok)
    return HealthStatus(score=ok, total=len(checks), summary=f"Health {ok}/{len(checks)} checks")


def recovery_guide(reason: str) -> list[str]:
    common = [
        "Re-run Host Preflight and resolve all FAIL checks.",
        "Confirm OpenCore and installer images exist in expected paths.",
        "Re-generate plan and compare against previous successful plan.",
    ]
    if "boot" in reason.lower():
        common.append("Check VM boot order and attached media in qm config.")
    if "asset" in reason.lower() or "iso" in reason.lower():
        common.append("Re-stage installer/recovery image and verify file size/checksum.")
    return common


def export_log_bundle() -> Path:
    out_dir = Path.cwd() / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle = out_dir / f"support-bundle-{ts}.tar.gz"

    include_paths = [
        out_dir / "logs",
        out_dir / "snapshots",
    ]

    with tarfile.open(bundle, "w:gz") as tar:
        for path in include_paths:
            if path.exists():
                tar.add(path, arcname=path.name)
    return bundle
